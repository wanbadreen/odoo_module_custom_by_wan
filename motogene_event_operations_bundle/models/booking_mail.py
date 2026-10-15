# -*- coding: utf-8 -*-
"""Optional, deduplicated booking-owner confirmation for group registrations.

The booking owner gets ONE summary for the whole booking, even when some
attendees have no personal email. This is independent of Odoo's native
per-attendee confirmation emails and is OFF by default per Event.
"""
from html import escape

from odoo import fields, models
from odoo.addons.motogene_event_self_service.models.self_service import (
    allowed_email, normalize_email,
)


class MotoGeneEventBookingConfirmation(models.Model):
    _inherit = "motogene.event.booking"

    motogene_ops_confirmation_mail_id = fields.Many2one(
        "mail.mail", string="Booking Owner Confirmation",
        readonly=True, copy=False, ondelete="set null",
        help="The one email queued for the verified booking owner. "
             "Presence of a mail record prevents accidental duplicates.",
    )

    def action_queue_owner_confirmation(self):
        for booking in self:
            event = booking.event_id
            if (not event.motogene_ops_booking_confirmation_enabled
                    or not event.motogene_contacts_enabled
                    or event.website_id != booking.website_id
                    or event.company_id != booking.company_id):
                continue
            recipient = normalize_email(booking.owner_email)
            if not allowed_email(recipient) or not booking.registration_ids:
                continue

            # Serialise attempts for the same group booking, and re-read
            # the idempotency marker after obtaining the lock.
            self.env.cr.execute(
                "SELECT id FROM motogene_event_booking WHERE id = %s FOR UPDATE",
                [booking.id],
            )
            booking.invalidate_recordset(["motogene_ops_confirmation_mail_id"])
            if booking.motogene_ops_confirmation_mail_id:
                continue

            lines = "".join(
                "<li>%s</li>" % escape(reg.name or "Participant")
                for reg in booking.registration_ids.sorted("id")
            )
            title = escape(event.name or "Event")
            body = (
                "<p>Hi,</p><p>Your booking for <b>%s</b> has been recorded.</p>"
                "<p>Participants:</p><ul>%s</ul>"
                "<p>To review or cancel an eligible registration, visit "
                "the MotoGene <b>Manage My Registration</b> page and verify "
                "this booking email address.</p>"
                "<p>Thank you,<br/>MotoGene Events</p>"
            ) % (title, lines)
            sender = booking.company_id.email or self.env.user.email or "noreply@localhost"
            mail = self.env["mail.mail"].sudo().create({
                "subject": "MotoGene Booking Confirmation - %s" % event.name,
                "email_to": recipient,
                "email_from": sender,
                "body_html": body,
                "auto_delete": False,
            })
            booking.sudo().write({"motogene_ops_confirmation_mail_id": mail.id})
            # Send only THIS mail after successful registration commit. It
            # never drains the global mail queue or activates any cron.
            mail.send_after_commit()
        return True
