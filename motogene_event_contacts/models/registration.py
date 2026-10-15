# -*- coding: utf-8 -*-
"""Identity-safe attendee Contacts; no changes to native 'Booked by' partner_id."""
import logging
import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.addons.motogene_event_self_service.models.self_service import allowed_email, normalize_email

_logger = logging.getLogger(__name__)


def normalized_name(value):
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


class EventRegistration(models.Model):
    _inherit = "event.registration"

    motogene_booking_id = fields.Many2one(
        "motogene.event.booking", string="MotoGene Booking", ondelete="set null",
        index=True, copy=False, readonly=True)
    motogene_attendee_contact_id = fields.Many2one(
        "res.partner", string="Attendee Contact", readonly=True, copy=False,
        help="Identity-matched participant, separate from Odoo's native Booked by field.")
    motogene_contact_sync_status = fields.Selection([
        ("off", "Not Enabled"), ("matched", "Matched"), ("created", "Created"),
        ("review", "Pending Review"), ("no_email", "No Individual Email"),
        ("failed", "Sync Failed"),
    ], default="off", string="Contact Sync", copy=False, readonly=True, index=True)
    motogene_contact_sync_error = fields.Char(copy=False, readonly=True)
    motogene_contact_review_ids = fields.One2many(
        "motogene.event.contact.review", "registration_id", readonly=True)

    @api.constrains("motogene_booking_id", "event_id")
    def _check_motogene_booking_scope(self):
        for reg in self:
            booking = reg.motogene_booking_id
            if booking and (booking.event_id != reg.event_id
                            or booking.website_id != reg.event_id.website_id
                            or booking.company_id != reg.event_id.company_id):
                raise ValidationError(_(
                    "A participant may only belong to a booking for the same "
                    "event, website, and company."
                ))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Existing registrations are NOT backfilled on module installation.
        # Contact failures never invalidate an otherwise successful booking.
        for registration in records:
            registration._motogene_attempt_contact_sync()
        return records

    def action_motogene_retry_contact_sync(self):
        for registration in self:
            registration._motogene_attempt_contact_sync()
        return True

    def _motogene_attempt_contact_sync(self):
        for registration in self:
            config = self.env["motogene.event.contact.config"].for_event(registration.event_id)
            if not config:
                continue
            try:
                with self.env.cr.savepoint():
                    registration._motogene_sync_attendee_contact(config)
            except Exception:
                _logger.exception(
                    "MotoGene attendee Contact sync failed: registration_id=%s",
                    registration.id,
                )
                registration.sudo().write({
                    "motogene_contact_sync_status": "failed",
                    "motogene_contact_sync_error": "Contact matching failed. Ask an administrator to review the logs.",
                })

    def _motogene_link_contact(self, partner, status):
        self.ensure_one()
        tags = self.event_id.motogene_contact_tag_ids
        if tags:
            # Additive only: never overwrite existing VIP/Customer tags.
            partner.sudo().write({"category_id": [(4, tag.id) for tag in tags if tag not in partner.category_id]})
        self.sudo().write({
            "motogene_attendee_contact_id": partner.id,
            "motogene_contact_sync_status": status,
            "motogene_contact_sync_error": False,
        })

    def _motogene_mark_review(self, reason, candidates, config):
        self.ensure_one()
        review_model = self.env["motogene.event.contact.review"].sudo()
        review = review_model.search([("registration_id", "=", self.id)], limit=1)
        vals = {
            "reason": reason,
            "state": "pending",
            "candidate_ids": [(6, 0, candidates.ids)],
            "company_id": self.event_id.company_id.id,
            "event_id": self.event_id.id,
        }
        if review:
            was_resolved = review.state == "resolved"
            review.write(vals)
        else:
            was_resolved = False
            review = review_model.create(dict(vals, registration_id=self.id))
        self.sudo().write({
            "motogene_attendee_contact_id": False,
            "motogene_contact_sync_status": "review",
            "motogene_contact_sync_error": False,
        })
        if config.review_user_id and (not review.activity_ids or was_resolved):
            review.activity_schedule(
                "mail.mail_activity_data_todo", user_id=config.review_user_id.id,
                summary="Review Event Contact Identity",
                note="Review the attendee name and candidate Contacts before linking.",
            )

    def _motogene_sync_attendee_contact(self, config):
        """Match a participant only by their own email AND their own name."""
        self.ensure_one()
        if self.motogene_contact_review_ids.filtered(lambda r: r.state == "pending"):
            return  # never override a pending human review on an automatic retry
        if self.motogene_attendee_contact_id and self.motogene_contact_sync_status in ("matched", "created"):
            # Preserve identity decisions already made by staff. Retrying
            # must never reopen a resolved name mismatch or create a Contact.
            self._motogene_link_contact(
                self.motogene_attendee_contact_id,
                self.motogene_contact_sync_status,
            )
            return
        own_email = normalize_email(self.email)
        if not own_email:
            self.sudo().write({
                "motogene_contact_sync_status": "no_email",
                "motogene_attendee_contact_id": False,
                "motogene_contact_sync_error": False,
            })
            return
        if not allowed_email(own_email) or not normalized_name(self.name):
            self._motogene_mark_review("invalid_identity", self.env["res.partner"], config)
            return

        # PostgreSQL transaction-level advisory lock prevents two simultaneous
        # website registrations for the same email from creating duplicates.
        lock_key = "motogene.event.contact.%s.%s" % (self.event_id.company_id.id, own_email)
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [lock_key])

        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        candidates = Partner.search([
            ("email_normalized", "=", own_email),
            ("company_id", "in", [False, self.event_id.company_id.id]),
        ])
        if len(candidates) > 1:
            self._motogene_mark_review("multiple_contacts", candidates, config)
            return
        if candidates:
            candidate = candidates[:1]
            if not candidate.active or normalized_name(candidate.name) != normalized_name(self.name):
                self._motogene_mark_review("name_mismatch", candidates, config)
                return
            self._motogene_link_contact(candidate, "matched")
            return

        vals = {
            "name": (self.name or "").strip(),
            "email": own_email,
            "company_id": self.event_id.company_id.id,
            "company_type": "person",
        }
        if self.phone:
            vals["phone"] = self.phone
        partner = Partner.create(vals)
        self._motogene_link_contact(partner, "created")
