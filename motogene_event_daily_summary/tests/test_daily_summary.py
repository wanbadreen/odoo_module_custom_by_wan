# -*- coding: utf-8 -*-
import base64
import io
import zipfile
from datetime import datetime, timedelta

from odoo.tests.common import TransactionCase


class TestMotoGeneDailySummary(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].search([], limit=1)
        if not cls.website:
            cls.website = cls.env["website"].create({
                "name": "Daily Summary Test Website",
                "domain": "daily-summary-test.example.com",
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
