# -*- coding: utf-8 -*-
"""Combined release backend tests. No SMTP or cron activation."""
import io
import zipfile
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase


class TestMotoGeneOperationsBundle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].create({
            "name": "MotoGene Operations QA Website",
            "domain": "operations-qa.example.invalid",
            "company_id": cls.env.company.id,
        })
        start = fields.Datetime.to_datetime(fields.Datetime.now()) + timedelta(days=12)
        cls.event = cls.env["event.event"].create({
            "name": "MotoGene Operations QA",
            "website_id": cls.website.id,
            "company_id": cls.env.company.id,
            "date_begin": start,
            "date_end": start + timedelta(hours=2),
        })
        cls.staff = cls.env["res.partner"].create({
            "name": "MotoGene QA Staff",
            "email": "qa-staff@example.invalid",
        })
        cls.daily = cls.env["motogene.event.daily.config"].create({
            "name": "MotoGene Operations QA Config",
            "company_id": cls.env.company.id,
            "website_id": cls.website.id,
            "recipient_ids": [(4, cls.staff.id)],
        })

    def _register(self, name, email=False, event=False):
        Model = type(self.env["event.registration"])
        with patch.object(Model, "_update_mail_schedulers", autospec=True):
            return self.env["event.registration"].create({
                "event_id": (event or self.event).id,
                "name": name, "email": email or False,
                "partner_id": False,
            })

    def _ops_todos(self, registration, summary):
        return self.env["mail.activity"].sudo().search([
            ("res_model_id", "=", self.env["ir.model"]._get_id("event.registration")),
            ("res_id", "=", registration.id),
            ("summary", "=", summary),
        ])

    def test_manual_export_without_email(self):
        r = self._register("QA Export", "qa-export@example.invalid")
        wizard = self.env["motogene.event.export.wizard"].create({
            "scope": "event", "event_id": self.event.id,
        })
        before = self.env["mail.mail"].search_count([])
        result = wizard.action_export_xlsx()
        self.assertEqual(result["type"], "ir.actions.act_url")
        self.assertTrue(wizard.file_data)
        self.assertEqual(self.env["mail.mail"].search_count([]), before)
        self.assertTrue(r.event_id)

    def test_report_has_booking_email_and_questions(self):
        r = self._register("QA Choice", "qa-choice@example.invalid")
        question = self.env["event.question"].create({
            "event_id": self.event.id, "title": "Preferred Topic",
            "question_type": "simple_choice",
        })
        choice = self.env["event.question.answer"].create({
            "question_id": question.id, "name": "=ALERT",
        })
        self.env["event.registration.answer"].create({
            "registration_id": r.id, "question_id": question.id,
            "value_answer_id": choice.id,
        })
        raw = self.daily._build_xlsx(r, r)
        z = zipfile.ZipFile(io.BytesIO(raw))
        strings = z.read("xl/sharedStrings.xml").decode("utf-8")
        self.assertIn("Preferred Topic", strings)
        self.assertIn("Booking Email", strings)
        self.assertIn("=ALERT", strings)
        data = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertNotIn("<f>", data)

    def test_registration_todo_disabled_by_default(self):
        self.event.motogene_ops_activity_user_id = self.env.user
        r = self._register("QA Default Off", "qa-off@example.invalid")
        self.assertFalse(r.activity_ids.filtered(
            lambda a: a.summary == "MotoGene Event — New Registration"
        ))

    def test_staff_new_registration_todo_once(self):
        staff_user = self.env.ref("base.user_admin")
        self.assertTrue(staff_user.active)
        self.event.write({
            "motogene_ops_notify_new": True,
            "motogene_ops_activity_user_id": staff_user.id,
        })
        r = self._register("QA New ToDo", "qa-todo@example.invalid")
        summary = "MotoGene Event — New Registration"
        scheduled = self._ops_todos(r, summary)
        self.assertEqual(len(scheduled), 1, "Missing To Do after registration create")
        self.assertEqual(scheduled.user_id, staff_user)
        r._motogene_staff_activity("new")
        self.assertEqual(len(self._ops_todos(r, summary)), 1)

    def test_cancellation_todo_is_opt_in(self):
        staff_user = self.env.ref("base.user_admin")
        self.assertTrue(staff_user.active)
        self.event.write({
            "motogene_ops_notify_cancel": True,
            "motogene_ops_activity_user_id": staff_user.id,
        })
        r = self._register("QA Cancel ToDo")
        r.action_cancel()
        self.assertEqual(r.state, "cancel")
        scheduled = self._ops_todos(r, "MotoGene Event — Cancellation")
        self.assertEqual(len(scheduled), 1, "Missing cancellation To Do")
        self.assertEqual(scheduled.user_id, staff_user)

    def test_native_email_readiness_does_not_send(self):
        self._register("QA No Email")
        before = self.env["mail.mail"].search_count([])
        report = self.event.motogene_ops_email_audit
        self.assertIn("without individual email: 1", report)
        self.assertIn("Native Odoo", report)
        action = self.event.action_motogene_ops_email_check()
        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(self.env["mail.mail"].search_count([]), before)

    def test_staff_recipients_union(self):
        self.event.motogene_ops_staff_ids = [(4, self.staff.id)]
        config = self.env["motogene.event.self.service.config"].create({
            "name": "Ops Recipient QA",
            "website_id": self.website.id,
            "company_id": self.env.company.id,
        })
        self.assertIn("qa-staff@example.invalid", config._staff_emails(self.event))

    def test_activity_sync_keeps_staff_contacts(self):
        begin = fields.Datetime.to_datetime(fields.Datetime.now()) + timedelta(days=18)
        activity = self.env["motogene.event.activity"].create({
            "name": "Operations QA Activity",
            "company_id": self.env.company.id,
            "website_id": self.website.id,
            "notification_partner_ids": [(4, self.staff.id)],
            "session_ids": [(0, 0, {
                "name": "Session A", "date_begin": begin,
                "date_end": begin + timedelta(hours=2), "seats_max": 20,
            })],
        })
        activity.action_prepare_sessions()
        child = activity.session_ids.mapped("event_id")
        self.assertEqual(len(child), 1)
        self.assertIn(self.staff, child.motogene_ops_staff_ids)

    def test_booking_owner_confirmation_off_by_default(self):
        attendee = self._register("QA Booker Attendee", False)
        booking = self.env["motogene.event.booking"].create({
            "event_id": self.event.id,
            "website_id": self.website.id,
            "company_id": self.env.company.id,
            "owner_email": "qa-owner@example.invalid",
        })
        attendee.write({"motogene_booking_id": booking.id})
        before = self.env["mail.mail"].search_count([])
        booking.action_queue_owner_confirmation()
        self.assertFalse(booking.motogene_ops_confirmation_mail_id)
        self.assertEqual(self.env["mail.mail"].search_count([]), before)

    def test_booking_owner_confirmation_one_email_and_safe_html(self):
        r1 = self._register("QA Booker", "qa-booker-own@example.invalid")
        r2 = self._register("<QA Attendee No Email>")
        booking = self.env["motogene.event.booking"].create({
            "event_id": self.event.id, "website_id": self.website.id,
            "company_id": self.env.company.id,
            "owner_email": "qa-owner-group@example.invalid",
        })
        (r1 | r2).write({"motogene_booking_id": booking.id})
        self.event.write({
            "motogene_contacts_enabled": True,
            "motogene_ops_booking_confirmation_enabled": True,
        })
        Mail = type(self.env["mail.mail"])
        with patch.object(Mail, "send_after_commit", autospec=True) as sender:
            booking.action_queue_owner_confirmation()
            booking.action_queue_owner_confirmation()
            sender.assert_called_once()
        mail = booking.motogene_ops_confirmation_mail_id
        self.assertTrue(mail)
        self.assertEqual(mail.email_to, "qa-owner-group@example.invalid")
        self.assertIn("QA Booker", mail.body_html)
        self.assertIn("&lt;QA Attendee No Email&gt;", mail.body_html)
        self.assertNotIn("<QA Attendee No Email>", mail.body_html)
