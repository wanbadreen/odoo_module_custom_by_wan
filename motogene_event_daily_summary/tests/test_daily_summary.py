# -*- coding: utf-8 -*-
import io
import zipfile
from datetime import datetime, timedelta

import pytz
from odoo import fields

from odoo.tests.common import TransactionCase


class TestMotoGeneDailySummary(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].create({
            "name": "Daily Summary QA Website",
            "domain": "daily-summary-qa.example.invalid",
            "company_id": cls.env.company.id,
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Daily Summary Test Recipient",
            "email": "staff-daily@example.com",
        })
        cls.config = cls.env["motogene.event.daily.config"].create({
            "name": "Daily Summary Test",
            "company_id": cls.website.company_id.id,
            "website_id": cls.website.id,
            "recipient_ids": [(6, 0, cls.partner.ids)],
        })

    def test_empty_day_is_skipped_and_idempotent(self):
        day = (datetime.utcnow() - timedelta(days=30)).date()
        first = self.config._generate_for_date(day)
        second = self.config._generate_for_date(day)
        self.assertEqual(first, second)
        self.assertEqual(first.state, "skipped")
        self.assertFalse(first.mail_id)

    def test_unique_recipient_and_invalid_email(self):
        second = self.env["res.partner"].create({
            "name": "Same Recipient", "email": "STAFF-DAILY@example.com",
        })
        invalid = self.env["res.partner"].create({"name": "Invalid", "email": "not-an-email"})
        self.config.recipient_ids = [(6, 0, (self.partner | second | invalid).ids)]
        self.assertEqual(len(self.config._get_recipients()), 1)

    def test_xlsx_contains_two_tabs(self):
        raw = self.config._build_xlsx(
            self.env["event.registration"], self.env["event.registration"])
        archive = zipfile.ZipFile(io.BytesIO(raw))
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        self.assertIn("New Registrations", workbook)
        self.assertIn("Full Attendee List", workbook)

    def _make_registration(self):
        now = fields.Datetime.now()
        event = self.env["event.event"].create({
            "name": "Daily Summary QA Event",
            "date_begin": now + timedelta(days=1),
            "date_end": now + timedelta(days=1, hours=2),
            "website_id": self.website.id,
            "company_id": self.website.company_id.id,
        })
        registration = self.env["event.registration"].create({
            "event_id": event.id,
            "name": "Test Attendee",
            "email": "attendee@example.com",
            "phone": "+60123456789",
        })
        day = datetime.now(pytz.timezone("Asia/Kuala_Lumpur")).date()
        return event, registration, day

    def test_failed_report_can_retry_without_duplicate_mail(self):
        _, _, day = self._make_registration()
        self.config.recipient_ids = [(5, 0, 0)]
        failed = self.config._generate_for_date(day)
        self.assertEqual(failed.state, "failed")
        self.assertFalse(failed.mail_id)
        self.assertEqual(failed.attempt_count, 1)
        self.config.recipient_ids = [(6, 0, self.partner.ids)]
        retried = self.config._generate_for_date(day)
        self.assertEqual(retried.id, failed.id)
        self.assertEqual(retried.state, "queued")
        self.assertEqual(retried.attempt_count, 2)
        self.assertTrue(retried.attachment_id)
        self.assertEqual(self.config._generate_for_date(day).mail_id, retried.mail_id)
        self.assertEqual(self.env["mail.mail"].search_count([("id", "=", retried.mail_id.id)]), 1)

    def test_company_and_website_scope(self):
        event, reg, day = self._make_registration()
        summary = self.config._generate_for_date(day)
        self.assertEqual(summary.state, "queued")
        self.assertEqual(summary.new_count, 1)
        self.assertEqual(summary.event_count, 1)
        self.assertEqual(reg.event_id.company_id, self.config.company_id)
        self.assertEqual(reg.event_id.website_id, self.config.website_id)

    def test_approval_required_and_nextcall_utc(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.config.write({"enabled": True})
        self.config.write({"send_time_confirmed": True, "send_hour": 9, "send_minute": 0})
        run_utc = self.config._next_scheduled_run_utc(datetime(2026, 10, 14, 2, 0, 0))
        self.assertEqual(run_utc, datetime(2026, 10, 15, 1, 0, 0))
        cron = self.env.ref("motogene_event_daily_summary.ir_cron_motogene_event_daily_summary")
        was_active = cron.active
        self.config.enabled = True
        self.assertEqual(cron.active, was_active)
        self.assertTrue(cron.nextcall)
