# -*- coding: utf-8 -*-
"""Manual, read-only XLSX export for a selected Event or Activity.

Generates a downloadable binary on a transient wizard. No mail.mail records,
no scheduled actions and no outgoing email are created.
"""
from datetime import datetime, time, timedelta

import pytz
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class MotoGeneEventExportWizard(models.TransientModel):
    _name = "motogene.event.export.wizard"
    _description = "MotoGene Event Attendee Excel Export"

    scope = fields.Selection([
        ("event", "Single Event / Session"),
        ("activity", "Activity (All Sessions)"),
    ], default="event", required=True)
    event_id = fields.Many2one("event.event", string="Event")
    activity_id = fields.Many2one(
        "motogene.event.activity", string="Activity"
    )
    date_from = fields.Date(
        help="Optional registration creation start date in Malaysia time."
    )
    date_to = fields.Date(
        help="Optional registration creation end date (inclusive) in Malaysia time."
    )
    include_cancelled = fields.Boolean(
        string="Include Cancelled / Draft Registrations", default=True
    )
    file_data = fields.Binary(readonly=True)
    file_name = fields.Char(readonly=True)

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for wizard in self:
            if wizard.date_from and wizard.date_to and wizard.date_from > wizard.date_to:
                raise ValidationError(_("The end date must not precede the start date."))

    def _events_to_export(self):
        self.ensure_one()
        if self.scope == "event":
            if not self.event_id:
                raise UserError(_("Choose an Event to export."))
            return self.event_id
        if not self.activity_id:
            raise UserError(_("Choose an Activity to export."))
        events = self.activity_id.session_ids.mapped("event_id")
        if not events:
            raise UserError(_("Prepare the Activity sessions before exporting."))
        return events

    def action_export_xlsx(self):
        self.ensure_one()
        if not self.env.user.has_group("event.group_event_user"):
            raise UserError(_("Only Events staff may export attendee details."))
        events = self._events_to_export()
        # Do not sudo attendee searches: keep Odoo's standard Event, company
        # and record visibility rules in force for an Events staff member.
        Registration = self.env["event.registration"]
        domain = [("event_id", "in", events.ids)]
        if not self.include_cancelled:
            domain.append(("state", "in", ["open", "done"]))
        all_regs = Registration.search(domain, order="event_id, id")
        new_regs = all_regs
        if self.date_from or self.date_to:
            zone = pytz.timezone("Asia/Kuala_Lumpur")
            if self.date_from:
                start = zone.localize(datetime.combine(
                    self.date_from, time.min,
                )).astimezone(pytz.UTC).replace(tzinfo=None)
                new_regs = new_regs.filtered(lambda r: r.create_date and r.create_date >= start)
            if self.date_to:
                end = zone.localize(datetime.combine(
                    self.date_to + timedelta(days=1), time.min,
                )).astimezone(pytz.UTC).replace(tzinfo=None)
                new_regs = new_regs.filtered(lambda r: r.create_date and r.create_date < end)

        # Reuse the same workbook builder as the daily email report without
        # creating a daily-summary configuration or queuing an email.
        config = self.env["motogene.event.daily.config"].new({
            "timezone": "Asia/Kuala_Lumpur",
        })
        xlsx = config._build_xlsx(new_regs, all_regs)
        import base64
        slug = "Activity" if self.scope == "activity" else "Event"
        filename = "MotoGene_%s_%s_Attendees.xlsx" % (
            slug, self.activity_id.id if self.scope == "activity" else self.event_id.id,
        )
        self.write({
            "file_data": base64.b64encode(xlsx),
            "file_name": filename,
        })
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/?model=motogene.event.export.wizard"
                   "&id=%s&field=file_data&filename_field=file_name&download=true" % self.id,
            "target": "self",
        }


class MotoGeneEventExportAction(models.Model):
    _inherit = "event.event"

    def action_motogene_export_attendees(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Export Event Attendees"),
            "res_model": "motogene.event.export.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_scope": "event",
                "default_event_id": self.id,
            },
        }


class MotoGeneActivityExportAction(models.Model):
    _inherit = "motogene.event.activity"

    def action_motogene_export_attendees(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Export Activity Attendees"),
            "res_model": "motogene.event.export.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_scope": "activity",
                "default_activity_id": self.id,
            },
        }
