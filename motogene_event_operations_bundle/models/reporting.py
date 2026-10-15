# -*- coding: utf-8 -*-
"""Extend the existing daily summary with booking/session and question columns.

No second mailing system is created; the existing deduplicated summary,
history, disabled cron and retry controls remain the single source of truth.
"""
import io
from collections import defaultdict

import pytz
from odoo import fields, models
from odoo.tools.misc import xlsxwriter

from odoo.addons.motogene_event_daily_summary.models.daily_summary import STATES


class MotoGeneDailySummaryRichExport(models.Model):
    _inherit = "motogene.event.daily.config"

    def _build_xlsx(self, new_registrations, all_registrations):
        self.ensure_one()
        all_regs = (new_registrations | all_registrations)
        answers = self.env["event.registration.answer"].search([
            ("registration_id", "in", all_regs.ids)
        ]) if all_regs else self.env["event.registration.answer"]
        titles = {}
        mapped_answers = defaultdict(list)
        for answer in answers:
            question = answer.question_id
            if question.question_type not in ("simple_choice", "text_box"):
                continue
            label = (question.title or "Custom Question").strip()
            key = (label, question.question_type)
            titles[key] = True
            value = answer.value_answer_id.name if answer.value_answer_id else answer.value_text_box
            if value:
                mapped_answers[(answer.registration_id.id, key)].append(value)
        question_keys = sorted(titles, key=lambda k: (k[0].casefold(), k[1]))
        headers = [
            "Activity", "Session", "Event", "Event Start (MYT)",
            "Event End (MYT)", "Participant (as per IC)", "Participant Email",
            "Booking Email", "Phone", "Ticket Type", "Registration Date (MYT)",
            "Status", "Attendee Contact", "Contact Sync",
        ] + [key[0] for key in question_keys]
        zone = pytz.timezone(self.timezone or "Asia/Kuala_Lumpur")

        def local(dt):
            if not dt:
                return ""
            timestamp = fields.Datetime.to_datetime(dt)
            if not timestamp.tzinfo:
                timestamp = pytz.UTC.localize(timestamp)
            return timestamp.astimezone(zone).strftime("%Y-%m-%d %H:%M")

        result = io.BytesIO()
        book = xlsxwriter.Workbook(result, {"in_memory": True})
        try:
            heading = book.add_format({
                "bold": True, "bg_color": "#DCE8F3", "text_wrap": True,
                "border": 1,
            })
            for title, records in (
                ("New Registrations", new_registrations),
                ("Full Attendee List", all_registrations),
            ):
                sheet = book.add_worksheet(title)
                sheet.freeze_panes(1, 0)
                sheet.set_row(0, 33)
                sheet.set_column(0, 2, 29)
                sheet.set_column(3, 4, 22)
                sheet.set_column(5, 7, 30)
                sheet.set_column(8, 13, 22)
                if len(headers) > 14:
                    sheet.set_column(14, len(headers) - 1, 32)
                for col, label in enumerate(headers):
                    sheet.write_string(0, col, label, heading)
                for row, reg in enumerate(records, 1):
                    event = reg.event_id
                    booking = reg.motogene_booking_id
                    vals = [
                        event.motogene_activity_id.name or "",
                        event.motogene_activity_session_id.name or "",
                        event.name or "",
                        local(event.date_begin),
                        local(event.date_end),
                        reg.name or "",
                        reg.email or "",
                        booking.owner_email if booking else (reg.email or ""),
                        reg.phone or "",
                        reg.event_ticket_id.name or "",
                        local(reg.create_date),
                        STATES.get(reg.state, reg.state or ""),
                        reg.motogene_attendee_contact_id.name or "",
                        reg.motogene_contact_sync_status or "",
                    ]
                    for key in question_keys:
                        vals.append("; ".join(mapped_answers.get((reg.id, key), [])))
                    for col, val in enumerate(vals):
                        # Never interpret attendee-entered strings as spreadsheet formulas.
                        sheet.write_string(row, col, str(val))
                sheet.autofilter(0, 0, max(len(records), 1), len(headers) - 1)
        finally:
            book.close()
        return result.getvalue()
