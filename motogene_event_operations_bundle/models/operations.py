# -*- coding: utf-8 -*-
"""Operational staff activities and email-readiness diagnostics.

Staff activity creation is OPT-IN per event (default OFF).
No SMTP / mail queue sending is added here. Native Odoo event.mail and the
existing guest cancellation mail flow are preserved as-is.
"""
from odoo import api, fields, models, _
from odoo.addons.motogene_event_self_service.models.self_service import allowed_email, normalize_email


class MotoGeneEventOperations(models.Model):
    _inherit = "event.event"

    motogene_ops_staff_ids = fields.Many2many(
        "res.partner", "motogene_ops_event_staff_rel", "event_id", "partner_id",
        string="Staff Notification Contacts",
        help="Existing guest cancellation notices can use these recipients. "
             "Adding contacts does not itself send any email."
    )
    motogene_ops_activity_user_id = fields.Many2one(
        "res.users", string="Internal Activity Assignee",
        help="Only used when the relevant internal staff To Do toggle is enabled."
    )
    motogene_ops_notify_new = fields.Boolean(
        string="To Do for New Registrations", default=False,
        help="Schedule an internal Odoo To Do for new registrations. Off by default."
    )
    motogene_ops_booking_confirmation_enabled = fields.Boolean(
        string="Send Booking Owner Confirmation", default=False,
        help="One email per completed group booking. Disabled by default; "
             "native Odoo attendee confirmations remain separate."
    )
    motogene_ops_notify_cancel = fields.Boolean(
        string="To Do for Cancellations", default=False,
        help="Schedule an internal Odoo To Do after a registration is cancelled. Off by default."
    )
    motogene_ops_email_audit = fields.Text(
        compute="_compute_motogene_ops_email_audit", string="Email Readiness",
        help="A diagnostic only: this does not enable, disable, or send native Odoo emails."
    )

    def _compute_motogene_ops_email_audit(self):
        for event in self:
            native = event.event_mail_ids.filtered(
                lambda schedule: schedule.interval_type == "after_sub"
                and schedule.template_ref
            )
            # Only look at event attendee records; do not disclose others' data.
            registered = self.env["event.registration"].sudo().search([
                ("event_id", "=", event.id), ("state", "in", ["open", "done"])
            ]) if event.id else self.env["event.registration"]
            blank = len(registered.filtered(lambda reg: not (reg.email or "").strip()))
            staff = event.motogene_ops_staff_ids.filtered(
                lambda p: allowed_email(normalize_email(p.email))
            )
            event.motogene_ops_email_audit = (
                "Native Odoo subscription template(s): %s\n"
                "Registration(s) without individual email: %s\n"
                "Valid staff notification Contacts: %s\n"
                "Registration To Do enabled: %s\n"
                "Cancellation To Do enabled: %s\n"
                "Booking-owner confirmation enabled: %s\n"
                "NOTE: This is a readiness summary only. Native Odoo's event.mail "
                "settings control outgoing confirmations; no queue or SMTP "
                "configuration is changed by this module."
            ) % (
                len(native), blank, len(staff),
                "Yes" if event.motogene_ops_notify_new else "No",
                "Yes" if event.motogene_ops_notify_cancel else "No",
                "Yes" if event.motogene_ops_booking_confirmation_enabled else "No",
            )

    def action_motogene_ops_email_check(self):
        self.ensure_one()
        text = self.motogene_ops_email_audit or _("No data")
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "title": _("MotoGene Email Readiness"),
                "message": text, "type": "warning", "sticky": True,
            },
        }


class MotoGeneActivityOperations(models.Model):
    _inherit = "motogene.event.activity"

    motogene_ops_booking_confirmation_enabled = fields.Boolean(
        string="Send Booking Owner Confirmation", default=False,
        help="Opt-in one booking owner summary email per completed registration group. "
             "Off by default."
    )

    def action_prepare_sessions(self):
        outcome = super().action_prepare_sessions()
        for activity in self:
            linked = activity.session_ids.mapped("event_id")
            if linked:
                # Activity already owns the staff recipients. Keep Studio field
                # propagation from the existing addon and add our fallback.
                linked.write({
                    "motogene_ops_staff_ids": [(6, 0, activity.notification_partner_ids.ids)],
                "motogene_ops_booking_confirmation_enabled":
                    activity.motogene_ops_booking_confirmation_enabled,
                })
        return outcome


class MotoGeneGuestStaffRecipients(models.Model):
    _inherit = "motogene.event.self.service.config"

    def _staff_emails(self, event):
        current = set(super()._staff_emails(event))
        for partner in event.motogene_ops_staff_ids:
            email = normalize_email(partner.email)
            if allowed_email(email):
                current.add(email)
        return sorted(current)


class MotoGeneRegistrationStaffActivities(models.Model):
    _inherit = "event.registration"

    @api.model_create_multi
    def create(self, vals_list):
        created = super().create(vals_list)
        for registration in created:
            if registration.state == "open":
                registration._motogene_staff_activity("new")
        return created

    def write(self, vals):
        before = self.filtered(lambda record: record.state != "cancel") if vals.get("state") == "cancel" else self.browse()
        done = super().write(vals)
        for reg in before.filtered(lambda record: record.state == "cancel"):
            reg._motogene_staff_activity("cancel")
        return done

    def _motogene_staff_activity(self, reason):
        type_xmlid = "mail.mail_activity_data_todo"
        model_id = self.env["ir.model"]._get_id("event.registration")
        todo_type = self.env.ref(type_xmlid, raise_if_not_found=False)
        if not todo_type:
            return
        for registration in self:
            event = registration.event_id
            person = event.motogene_ops_activity_user_id
            enabled = (
                event.motogene_ops_notify_new if reason == "new"
                else event.motogene_ops_notify_cancel
            )
            if not enabled or not person or not person.active:
                continue
            subject = (
                "MotoGene Event — New Registration" if reason == "new"
                else "MotoGene Event — Cancellation"
            )
            # Prevent concurrent workers / repeated create callbacks from
            # adding the exact same pending To Do task.
            domain = [
                ("res_model_id", "=", model_id),
                ("res_id", "=", registration.id),
                ("activity_type_id", "=", todo_type.id),
                ("user_id", "=", person.id),
                ("summary", "=", subject),
            ]
            if self.env["mail.activity"].sudo().search_count(domain):
                continue
            registration.sudo().activity_schedule(
                type_xmlid, user_id=person.id,
                summary=subject,
                note=_("Review registration %(registration)s (%(event)s).",
                       registration=registration.name or registration.id,
                       event=event.name),
            )
