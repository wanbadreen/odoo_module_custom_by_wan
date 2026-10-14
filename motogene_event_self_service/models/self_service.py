# -*- coding: utf-8 -*-
"""Private verification grants and cancellation business rules."""
import hashlib
import re
import secrets
from datetime import timedelta, datetime
from html import escape

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

_EMAIL = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")
_TOKEN_LIFETIME = timedelta(minutes=15)
_SESSION_LIFETIME = 30 * 60


def normalize_email(value):
    return (value or "").strip().casefold()


def allowed_email(value):
    return bool(value and len(value) <= 254 and _EMAIL.fullmatch(value))


def hash_value(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MotoGeneEventSelfServiceConfig(models.Model):
    _name = "motogene.event.self.service.config"
    _description = "MotoGene Guest Event Management Settings"

    name = fields.Char(required=True, default="MotoGene Global Self-Service")
    website_id = fields.Many2one("website", required=True, ondelete="restrict")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    enabled = fields.Boolean(default=False, help="Enable on staging after checking scope and dummy attendees.")
    notification_recipient_field = fields.Char(
        default="x_studio_event_notification_recipients", readonly=True,
        help="Use existing Event Notification Recipients Studio field for staff alerts, if present.",
    )
    _sql_constraints = [
        ("website_unique", "unique(website_id)", "Only one self-service configuration per website."),
    ]

    @api.constrains("website_id", "company_id")
    def _check_website_company(self):
        for record in self:
            if record.website_id.company_id and record.website_id.company_id != record.company_id:
                raise ValidationError(_("The configured company must match the website company."))

    @api.model
    def for_website(self, website):
        return self.sudo().search([
            ("website_id", "=", website.id), ("enabled", "=", True),
        ], limit=1)

    def _eligible_registration(self, registration):
        """Only free, still-registered attendees before the event start may cancel."""
        self.ensure_one()
        if not registration.exists():
            return False
        event = registration.event_id
        if not event or event.website_id != self.website_id or event.company_id != self.company_id:
            return False
        if registration.state != "open" or not event.date_begin:
            return False
        if fields.Datetime.to_datetime(event.date_begin) <= fields.Datetime.to_datetime(fields.Datetime.now()):
            return False
        ticket = registration.event_ticket_id
        if ticket and "price" in ticket._fields and ticket.price > 0:
            return False
        for related_name in ("sale_order_line_id", "sale_order_id"):
            if related_name in registration._fields and registration[related_name]:
                return False
        return True

    def _registrations_for_email(self, normalized):
        """Scope by company and website, then enforce exact normalized email equality."""
        self.ensure_one()
        if not allowed_email(normalized):
            return self.env["event.registration"].sudo().browse()
        candidates = self.env["event.registration"].sudo().search([
            ("event_id.website_id", "=", self.website_id.id),
            ("event_id.company_id", "=", self.company_id.id),
            ("email", "=ilike", normalized),
        ])
        return candidates.filtered(lambda r: normalize_email(r.email) == normalized)

    def _staff_emails(self, event):
        self.ensure_one()
        recipients = set()
        field = self.notification_recipient_field
        if field and field in event._fields:
            for partner in event[field]:
                email = normalize_email(partner.email)
                if allowed_email(email):
                    recipients.add(email)
        return sorted(recipients)

    def _cancel_for_verified_email(self, registration_id, verified_email):
        """Cancel after a verified session, with final ownership checks under row lock."""
        self.ensure_one()
        normalized = normalize_email(verified_email)
        if not allowed_email(normalized):
            raise AccessError(_("Access denied."))
        try:
            record_id = int(registration_id)
        except (TypeError, ValueError):
            raise AccessError(_("Access denied."))
        self.env.cr.execute("SELECT id FROM event_registration WHERE id = %s FOR UPDATE", [record_id])
        if not self.env.cr.fetchone():
            raise AccessError(_("Access denied."))
        registration = self.env["event.registration"].sudo().browse(record_id)
        registration.invalidate_recordset(["state", "event_id", "email"])
        if normalize_email(registration.email) != normalized:
            raise AccessError(_("Access denied."))
        if (registration.event_id.website_id != self.website_id
                or registration.event_id.company_id != self.company_id):
            raise AccessError(_("Access denied."))
        if not self._eligible_registration(registration):
            raise UserError(_("This registration cannot be cancelled online."))

        registration.action_cancel()
        event = registration.event_id
        title = escape(event.name or "Event")
        attendee = escape(registration.name or "Participant")
        sender = self.company_id.email or self.env.user.email or "noreply@localhost"
        self.env["mail.mail"].sudo().create({
            "subject": "MotoGene Event Cancellation - %s" % event.name,
            "body_html": (
                "<p>Hi %s,</p><p>Your registration for <b>%s</b> has been cancelled.</p>"
                "<p>If you need assistance, please contact the event organiser.</p>"
                "<p>MotoGene Events</p>"
            ) % (attendee, title),
            "email_to": normalized,
            "email_from": sender,
            "auto_delete": False,
        })
        staff = self._staff_emails(event)
        if staff:
            self.env["mail.mail"].sudo().create({
                "subject": "MotoGene - Participant Cancelled - %s" % event.name,
                "body_html": (
                    "<p>A participant cancelled their MotoGene event registration.</p>"
                    "<p><b>Event:</b> %s<br/><b>Participant:</b> %s<br/>"
                    "<b>Email:</b> %s<br/><b>Status:</b> Cancelled</p>"
                ) % (title, attendee, escape(normalized)),
                "email_to": ",".join(staff),
                "email_from": sender,
                "auto_delete": False,
            })
        return registration


class MotoGeneEventSelfServiceToken(models.Model):
    _name = "motogene.event.self.service.token"
    _description = "MotoGene Event Email Verification Grant"
    _order = "create_date desc"

    token_hash = fields.Char(required=True, index=True, readonly=True)
    email_hash = fields.Char(required=True, index=True, readonly=True)
    email_normalized = fields.Char(required=True, readonly=True)
    ip_hash = fields.Char(required=True, index=True, readonly=True)
    website_id = fields.Many2one("website", required=True, ondelete="cascade", readonly=True)
    expires_at = fields.Datetime(required=True, readonly=True)
    used_at = fields.Datetime(readonly=True)

    _sql_constraints = [
        ("token_hash_unique", "unique(token_hash)", "Verification grants must be unique."),
    ]

    @api.model
    def _issue_token(self, email, website, ip):
        """Issue a short-lived token, throttled by email and IP.

        Create a grant for unknown emails too; the controller only queues
        a verification email when a registration exists.
        """
        normalized = normalize_email(email)
        if not allowed_email(normalized):
            return False
        now = fields.Datetime.to_datetime(fields.Datetime.now())
        email_hash = hash_value(normalized)
        ip_hash = hash_value(ip or "unknown")
        Model = self.sudo()
        if Model.search_count([
            ("website_id", "=", website.id), ("email_hash", "=", email_hash),
            ("create_date", ">=", fields.Datetime.to_string(now - timedelta(minutes=15))),
        ]) >= 3:
            return False
        if Model.search_count([
            ("website_id", "=", website.id), ("ip_hash", "=", ip_hash),
            ("create_date", ">=", fields.Datetime.to_string(now - timedelta(hours=1))),
        ]) >= 12:
            return False
        token = secrets.token_urlsafe(32)
        Model.create({
            "token_hash": hash_value(token),
            "email_hash": email_hash,
            "email_normalized": normalized,
            "ip_hash": ip_hash,
            "website_id": website.id,
            "expires_at": now + _TOKEN_LIFETIME,
        })
        return token

    @api.model
    def _consume_token(self, raw_token, website):
        """Consume once under a PostgreSQL row lock."""
        if not raw_token or len(raw_token) > 200:
            return False
        token = self.sudo().search([
            ("token_hash", "=", hash_value(raw_token)),
            ("website_id", "=", website.id),
        ], limit=1)
        if not token:
            return False
        self.env.cr.execute(
            "SELECT id FROM motogene_event_self_service_token WHERE id = %s FOR UPDATE", [token.id],
        )
        token.invalidate_recordset(["used_at", "expires_at"])
        if token.used_at or fields.Datetime.to_datetime(token.expires_at) <= fields.Datetime.to_datetime(fields.Datetime.now()):
            return False
        token.write({"used_at": fields.Datetime.now()})
        return token.email_normalized

    @api.model
    def _cleanup_expired(self):
        """Manual/admin-only token housekeeping (not scheduled at installation)."""
        cutoff = fields.Datetime.to_datetime(fields.Datetime.now()) - timedelta(days=30)
        self.sudo().search([("expires_at", "<", fields.Datetime.to_string(cutoff))]).unlink()
