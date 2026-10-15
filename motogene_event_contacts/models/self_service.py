# -*- coding: utf-8 -*-
"""Extend verified guest access without weakening legacy email ownership."""
from html import escape
from odoo import models, _
from odoo.exceptions import AccessError, UserError

from odoo.addons.motogene_event_self_service.models.self_service import normalize_email, allowed_email


class MotoGeneEventSelfServiceConfig(models.Model):
    _inherit = "motogene.event.self.service.config"

    def _registrations_for_email(self, normalized):
        registrations = super()._registrations_for_email(normalized)
        self.ensure_one()
        normalized = normalize_email(normalized)
        if not allowed_email(normalized):
            return registrations
        bookings = self.env["motogene.event.booking"].sudo().search([
            ("owner_email", "=", normalized),
            ("website_id", "=", self.website_id.id),
            ("company_id", "=", self.company_id.id),
        ])
        return registrations | bookings.mapped("registration_ids").filtered(
            lambda r: r.event_id.website_id == self.website_id
            and r.event_id.company_id == self.company_id
        )

    def _motogene_can_manage_registration(self, reg, normalized):
        self.ensure_one()
        email = normalize_email(normalized)
        if not allowed_email(email) or not reg.exists():
            return False
        if reg.event_id.website_id != self.website_id or reg.event_id.company_id != self.company_id:
            return False
        if normalize_email(reg.email) == email:
            return True
        booking = reg.motogene_booking_id
        return bool(booking and booking.website_id == self.website_id
                    and booking.company_id == self.company_id
                    and booking.owner_email == email
                    and booking.event_id == reg.event_id)

    def _cancel_for_verified_email(self, registration_id, verified_email):
        """Owner may cancel one child registration after verifying booking email.

        Keep native direct-attendee flow entirely unchanged.
        """
        self.ensure_one()
        email = normalize_email(verified_email)
        if not allowed_email(email):
            raise AccessError(_("Access denied."))
        try:
            record_id = int(registration_id)
        except (TypeError, ValueError):
            raise AccessError(_("Access denied."))

        self.env.cr.execute(
            "SELECT id FROM event_registration WHERE id = %s FOR UPDATE", [record_id],
        )
        if not self.env.cr.fetchone():
            raise AccessError(_("Access denied."))
        reg = self.env["event.registration"].sudo().browse(record_id)
        reg.invalidate_recordset(["state", "event_id", "email", "motogene_booking_id"])
        if not self._motogene_can_manage_registration(reg, email):
            raise AccessError(_("Access denied."))
        if normalize_email(reg.email) == email:
            return super()._cancel_for_verified_email(registration_id, email)
        if not self._eligible_registration(reg):
            raise UserError(_("This registration cannot be cancelled online."))

        reg.action_cancel()
        event = reg.event_id
        sender = self.company_id.email or self.env.user.email or "noreply@localhost"
        customer_mail = self.env["mail.mail"].sudo().create({
            "subject": "MotoGene Event Cancellation - %s" % event.name,
            "body_html": (
                "<p>Hi,</p><p>The registration for <b>%s</b> at <b>%s</b> "
                "has been cancelled.</p><p>MotoGene Events</p>"
            ) % (escape(reg.name or "Participant"), escape(event.name or "Event")),
            "email_to": email,
            "email_from": sender,
            "auto_delete": False,
        })
        outgoing = customer_mail
        staff = self._staff_emails(event)
        if staff:
            staff_mail = self.env["mail.mail"].sudo().create({
                "subject": "MotoGene - Participant Cancelled - %s" % event.name,
                "body_html": (
                    "<p>One participant was cancelled by their booking owner.</p>"
                    "<p><b>Event:</b> %s<br/><b>Participant:</b> %s<br/>"
                    "<b>Booking email:</b> %s</p>"
                ) % (escape(event.name or "Event"), escape(reg.name or "Participant"),
                     escape(email)),
                "email_to": ",".join(staff),
                "email_from": sender,
                "auto_delete": False,
            })
            outgoing |= staff_mail
        # Preserve the existing self-service sender behavior; no global cron
        # or legacy mail queue processing is enabled.
        outgoing.send_after_commit()
        return reg
