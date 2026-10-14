# -*- coding: utf-8 -*-
import base64
import io
import logging
import re
from datetime import datetime, time, timedelta

import pytz
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import xlsxwriter

_logger = logging.getLogger(__name__)

STATES = {"draft": "Unconfirmed", "open": "Registered", "done": "Attended", "cancel": "Cancelled"}
EMAIL_PATTERN = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")


class MotoGeneEventDailyConfig(models.Model):
    _name = "motogene.event.daily.config"
    _description = "MotoGene Daily Event Summary Configuration"
    _rec_name = "name"

    name = fields.Char(required=True, default="MotoGene Global Daily Summary")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    website_id = fields.Many2one("website", required=True, ondelete="restrict")
    recipient_ids = fields.Many2many("res.partner", string="Global Staff Recipients",
                                     help="Only these global recipients receive the combined report.")
    enabled = fields.Boolean(default=False, help="Disabled by default. Enable only after staging QA and send-time approval.")
    timezone = fields.Selection([("Asia/Kuala_Lumpur", "Malaysia (Asia/Kuala_Lumpur)")],
                                default="Asia/Kuala_Lumpur", required=True)
    send_hour = fields.Integer(default=9, help="Placeholder only; actual cron nextcall must be set after approval.")
    send_minute = fields.Integer(default=0)
    summary_ids = fields.One2many("motogene.event.daily.summary", "config_id", readonly=True)

    _sql_constraints = [
        ("website_unique", "unique(website_id)", "Only one daily summary configuration is allowed per website."),
    ]

    @api.constrains("company_id", "website_id", "send_hour", "send_minute")
    def _check_configuration(self):
        for rec in self:
            if rec.website_id and rec.website_id.company_id != rec.company_id:
                raise ValidationError(_("The website must belong to the selected company."))
            if not 0 <= rec.send_hour <= 23 or not 0 <= rec.send_minute <= 59:
                raise ValidationError(_("The send time must be between 00:00 and 23:59."))

    def _local_yesterday(self):
        self.ensure_one()
        return (datetime.now(pytz.timezone(self.timezone)) - timedelta(days=1)).date()

    def _date_bounds_utc(self, report_date):
        self.ensure_one()
        zone = pytz.timezone(self.timezone)
        start = zone.localize(datetime.combine(report_date, time.min)).astimezone(pytz.UTC)
        end = zone.localize(datetime.combine(report_date + timedelta(days=1), time.min)).astimezone(pytz.UTC)
        return start.replace(tzinfo=None), end.replace(tzinfo=None)

    def _get_recipients(self):
        self.ensure_one()
        addresses = {}
        for partner in self.recipient_ids:
            email = (partner.email or "").strip()
            if email and EMAIL_PATTERN.match(email):
                addresses[email.casefold()] = email
            elif email:
                _logger.warning("Skipping invalid email for contact ID %s in MotoGene daily report", partner.id)
        return sorted(addresses.values(), key=str.lower)

    @api.model
    def _cron_generate(self):
        # Created inactive; no outgoing email is generated until an admin enables the config AND cron.
        for config in self.sudo().search([("enabled", "=", True)]):
            try:
                config._generate_for_date(config._local_yesterday())
            except Exception:
                _logger.exception("MotoGene daily summary failure for config ID %s", config.id)

    def action_generate_previous_day(self):
        """Admin-controlled queue generation. Never calls mail.send()."""
        for config in self:
            config._generate_for_date(config._local_yesterday())
        return True

    def _generate_for_date(self, report_date):
        self.ensure_one()
        report_date = fields.Date.to_date(report_date)
        # Prevent concurrent scheduled/manual generation for same config and report date.
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", (self.id, report_date.toordinal()))
        Summary = self.env["motogene.event.daily.summary"].sudo()
        existing = Summary.search([("config_id", "=", self.id), ("report_date", "=", report_date)], limit=1)
        if existing:
            return existing

        start, end = self._date_bounds_utc(report_date)
        Registration = self.env["event.registration"].sudo()
        new_registrations = Registration.search([
            ("create_date", ">=", fields.Datetime.to_string(start)),
            ("create_date", "<", fields.Datetime.to_string(end)),
            ("event_id.website_id", "=", self.website_id.id),
        ], order="event_id, id")
        if not new_registrations:
            return Summary.create({
                "config_id": self.id, "report_date": report_date,
                "state": "skipped", "notes": "No new registrations for this reporting date.",
            })

        recipients = self._get_recipients()
        if not recipients:
            return Summary.create({
                "config_id": self.id, "report_date": report_date,
                "state": "failed", "new_count": len(new_registrations),
                "notes": "No valid global staff recipients. No email queued.",
            })

        events = new_registrations.mapped("event_id")
        all_registrations = Registration.search([("event_id", "in", events.ids)], order="event_id, id")
        active = all_registrations.filtered(lambda r: r.state in ("open", "done"))
        summary = Summary.create({
            "config_id": self.id, "report_date": report_date,
            "state": "generating", "new_count": len(new_registrations),
            "active_count": len(active), "event_count": len(events),
            "recipient_snapshot": ", ".join(recipients),
        })
        try:
            xlsx_content = self._build_xlsx(new_registrations, all_registrations)
            attachment = self.env["ir.attachment"].sudo().create({
                "name": "MotoGene_Daily_Summary_%s.xlsx" % report_date.isoformat(),
                "type": "binary",
                "datas": base64.b64encode(xlsx_content),
                "res_model": summary._name,
                "res_id": summary.id,
                "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            })
            rows = []
            for event in events.sorted(lambda e: (e.date_begin or fields.Datetime.now(), e.id)):
                event_regs = all_registrations.filtered(lambda r: r.event_id == event)
                new_count = len(new_registrations.filtered(lambda r: r.event_id == event))
                active_count = len(event_regs.filtered(lambda r: r.state in ("open", "done")))
                rows.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    __import__("html").escape(event.name or ""), new_count, active_count))
            body = (
                "<p>Hi Team,</p><p>Here is the MotoGene event registration summary for %s.</p>"
                "<p>New registrations: <b>%s</b> | Events: <b>%s</b> | Active attendees: <b>%s</b></p>"
                "<table border='1' cellpadding='5'><tr><th>Event</th><th>New</th><th>Active</th></tr>%s</table>"
                "<p>The attached Excel includes New Registrations and Full Attendee List.</p>"
                "<p>Thank you,<br/>MotoGene Event Registration System</p>"
            ) % (report_date.isoformat(), len(new_registrations), len(events), len(active), "".join(rows))
            mail = self.env["mail.mail"].sudo().create({
                "subject": "MotoGene - Daily Event Registration Summary (%s)" % report_date.isoformat(),
                "body_html": body,
                "email_to": ",".join(recipients),
                "email_from": self.company_id.email or self.env.user.email or "noreply@localhost",
                "auto_delete": False,
                "attachment_ids": [(4, attachment.id)],
            })
            summary.write({"state": "queued", "attachment_id": attachment.id, "mail_id": mail.id})
        except Exception as exc:
            _logger.exception("Unable to generate MotoGene daily event summary ID %s", summary.id)
            summary.write({"state": "failed", "notes": str(exc)[:1000]})
        return summary

    def _build_xlsx(self, new_registrations, all_registrations):
        self.ensure_one()
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        try:
            heading = workbook.add_format({"bold": True, "bg_color": "#DDEBF7"})
            headers = ["Event", "Event Start (MYT)", "Event End (MYT)", "Participant (as per IC)",
                       "Email", "Phone", "Ticket Type", "Registration Date (MYT)", "Status"]
            zone = pytz.timezone(self.timezone)

            def local(dt):
                if not dt:
                    return ""
                value = fields.Datetime.to_datetime(dt)
                return pytz.UTC.localize(value).astimezone(zone).strftime("%Y-%m-%d %H:%M")

            for title, registrations in (
                ("New Registrations", new_registrations),
                ("Full Attendee List", all_registrations),
            ):
                sheet = workbook.add_worksheet(title)
                sheet.freeze_panes(1, 0)
                sheet.set_column(0, 0, 34)
                sheet.set_column(1, 2, 20)
                sheet.set_column(3, 3, 28)
                sheet.set_column(4, 5, 27)
                sheet.set_column(6, 8, 23)
                for col, label in enumerate(headers):
                    sheet.write(0, col, label, heading)
                for row_num, reg in enumerate(registrations, 1):
                    values = [
                        reg.event_id.name or "", local(reg.event_id.date_begin),
                        local(reg.event_id.date_end), reg.name or "", reg.email or "",
                        reg.phone or "", reg.event_ticket_id.name or "",
                        local(reg.create_date), STATES.get(reg.state, reg.state or ""),
                    ]
                    for col, value in enumerate(values):
                        # write_string prevents spreadsheet formula injection from untrusted registrations.
                        sheet.write_string(row_num, col, str(value))
                sheet.autofilter(0, 0, max(len(registrations), 1), len(headers) - 1)
        finally:
            workbook.close()
        return output.getvalue()


class MotoGeneEventDailySummary(models.Model):
    _name = "motogene.event.daily.summary"
    _description = "MotoGene Daily Event Summary History"
    _order = "report_date desc, id desc"

    config_id = fields.Many2one("motogene.event.daily.config", required=True, ondelete="restrict")
    report_date = fields.Date(required=True, index=True)
    state = fields.Selection([
        ("generating", "Generating"), ("skipped", "Skipped"),
        ("queued", "Queued"), ("failed", "Failed"),
    ], required=True, default="generating", readonly=True)
    event_count = fields.Integer(readonly=True)
    new_count = fields.Integer(readonly=True)
    active_count = fields.Integer(readonly=True)
    recipient_snapshot = fields.Text(readonly=True)
    notes = fields.Text(readonly=True)
    attachment_id = fields.Many2one("ir.attachment", readonly=True)
    mail_id = fields.Many2one("mail.mail", readonly=True)

    _sql_constraints = [
        ("unique_report", "unique(config_id, report_date)", "This report has already been generated for the date."),
    ]
