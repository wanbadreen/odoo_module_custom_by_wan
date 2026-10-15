# -*- coding: utf-8 -*-
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase

from ..models.self_service import hash_value


class TestMotoGeneGuestCancellation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].create({
            "name": "MotoGene Guest Test Website",
            "domain": "motogene-guest.example.invalid",
            "company_id": cls.env.company.id,
        })
        cls.other_website = cls.env["website"].create({
            "name": "Unrelated Website",
            "domain": "unrelated-guest.example.invalid",
            "company_id": cls.env.company.id,
        })
        cls.config = cls.env["motogene.event.self.service.config"].create({
            "name": "Guest Test Settings",
            "website_id": cls.website.id,
            "company_id": cls.env.company.id,
            "enabled": True,
        })
        start = fields.Datetime.to_datetime(fields.Datetime.now()) + timedelta(days=2)
        cls.event = cls.env["event.event"].create({
            "name": "Guest Cancellation Test Yoga",
            "date_begin": start,
            "date_end": start + timedelta(hours=2),
            "website_id": cls.website.id,
            "company_id": cls.env.company.id,
        })
        cls.registration = cls.env["event.registration"].create({
            "event_id": cls.event.id,
            "name": "Test Attendee",
            "email": "Customer.Example@example.com",
            "phone": "+60123456789",
        })
        cls.other_event = cls.env["event.event"].create({
            "name": "Other Website Guest Test",
            "date_begin": start,
            "date_end": start + timedelta(hours=2),
            "website_id": cls.other_website.id,
            "company_id": cls.env.company.id,
        })
        cls.other_reg = cls.env["event.registration"].create({
            "event_id": cls.other_event.id,
            "name": "Other Attendee",
            "email": "Customer.Example@example.com",
        })

    def test_email_verification_one_time_and_wrong_site(self):
        Token = self.env["motogene.event.self.service.token"]
        raw = Token._issue_token("Customer.Example@example.com", self.website, "127.0.0.1")
        self.assertTrue(raw)
        self.assertFalse(Token._consume_token(raw, self.other_website))
        self.assertEqual(Token._consume_token(raw, self.website), "customer.example@example.com")
        self.assertFalse(Token._consume_token(raw, self.website))

    def test_token_expiry_and_rate_limit(self):
        Token = self.env["motogene.event.self.service.token"]
        raw = Token._issue_token("new@example.com", self.website, "127.0.0.2")
        grant = Token.search([("token_hash", "=", hash_value(raw))])
        grant.write({
            "expires_at": fields.Datetime.to_datetime(fields.Datetime.now()) - timedelta(minutes=1),
        })
        self.assertFalse(Token._consume_token(raw, self.website))
        self.assertTrue(Token._issue_token("new@example.com", self.website, "127.0.0.2"))
        self.assertTrue(Token._issue_token("new@example.com", self.website, "127.0.0.2"))
        self.assertFalse(Token._issue_token("new@example.com", self.website, "127.0.0.2"))

    def test_email_ownership_filter_and_wrong_registration(self):
        self.assertEqual(
            self.config._registrations_for_email("customer.example@example.com"),
            self.registration,
        )
        with self.assertRaises(AccessError):
            self.config._cancel_for_verified_email(self.registration.id, "stranger@example.com")
        with self.assertRaises(AccessError):
            self.config._cancel_for_verified_email(self.other_reg.id, "customer.example@example.com")

    def test_customer_cancellation_and_no_duplicate_emails(self):
        partner = self.env["res.partner"].create({
            "name": "Event Staff", "email": "staff-alert@example.com",
        })
        if "x_studio_event_notification_recipients" in self.event._fields:
            self.event["x_studio_event_notification_recipients"] = [(6, 0, partner.ids)]
        before = self.env["mail.mail"].search_count([
            ("subject", "like", "MotoGene Event Cancellation - Guest Cancellation Test Yoga"),
        ])
        self.assertTrue(self.config._eligible_registration(self.registration))
        # Never connect to SMTP during module unit tests. Assert that the
        # native Odoo post-commit sender is scheduled for this exact batch.
        mail_model = type(self.env["mail.mail"])
        with patch.object(mail_model, "send_after_commit", autospec=True) as send_after_commit:
            self.config._cancel_for_verified_email(self.registration.id, "customer.example@example.com")
            send_after_commit.assert_called_once()
            queued_mails = send_after_commit.call_args.args[0]
            self.assertEqual(
                len(queued_mails),
                2 if "x_studio_event_notification_recipients" in self.event._fields else 1,
            )
            self.assertTrue(all(mail.state == "outgoing" for mail in queued_mails))
        self.assertEqual(self.registration.state, "cancel")
        after = self.env["mail.mail"].search_count([
            ("subject", "like", "MotoGene Event Cancellation - Guest Cancellation Test Yoga"),
        ])
        self.assertEqual(after, before + 1)
        with self.assertRaises(UserError):
            self.config._cancel_for_verified_email(self.registration.id, "customer.example@example.com")
        self.assertEqual(self.env["mail.mail"].search_count([
            ("subject", "like", "MotoGene Event Cancellation - Guest Cancellation Test Yoga"),
        ]), after)

    def test_attended_and_event_started_cannot_cancel(self):
        self.registration.action_set_done()
        self.assertFalse(self.config._eligible_registration(self.registration))
        self.registration.action_confirm()
        past = fields.Datetime.to_datetime(fields.Datetime.now()) - timedelta(hours=1)
        self.event.write({"date_begin": past, "date_end": past + timedelta(minutes=30)})
        self.assertFalse(self.config._eligible_registration(self.registration))

    def test_url_config_scope_disabled(self):
        self.assertEqual(
            self.env["motogene.event.self.service.config"].for_website(self.website).ids,
            self.config.ids,
        )
        self.config.enabled = False
        self.assertFalse(self.env["motogene.event.self.service.config"].for_website(self.website))

    def test_multiple_attendees_with_same_verified_email(self):
        another = self.env["event.registration"].create({
            "event_id": self.event.id,
            "name": "Second Participant",
            "email": "CUSTOMER.EXAMPLE@example.com",
        })
        regs = self.config._registrations_for_email("customer.example@example.com")
        self.assertEqual(set(regs.ids), {self.registration.id, another.id})
        self.assertNotIn(self.other_reg.id, regs.ids)

    def test_private_staff_recipient_resolution(self):
        partner = self.env["res.partner"].create({
            "name": "Alert Recipient", "email": "Staff-Alert@example.com",
        })
        if "x_studio_event_notification_recipients" in self.event._fields:
            self.event["x_studio_event_notification_recipients"] = [(6, 0, partner.ids)]
            self.assertEqual(self.config._staff_emails(self.event), ["staff-alert@example.com"])
        else:
            self.assertEqual(self.config._staff_emails(self.event), [])

    def test_cancellation_unavailable_reason_after_event_start(self):
        """Still-registered attendees should see why cancellation is closed."""
        past = fields.Datetime.to_datetime(fields.Datetime.now()) - timedelta(hours=2)
        self.event.write({
            "date_begin": past,
            "date_end": past + timedelta(hours=1),
        })
        self.assertFalse(self.config._eligible_registration(self.registration))
        self.assertIn(
            "already started",
            self.config._cancellation_unavailable_reason(self.registration),
        )

    def test_upcoming_free_ticket_has_no_cancellation_warning(self):
        ticket = self.env["event.event.ticket"].create({
            "name": "QA Free Admission",
            "event_id": self.event.id,
            "price": 0.0,
        })
        self.registration.event_ticket_id = ticket
        self.assertEqual(ticket.price, 0.0)
        self.assertTrue(self.config._eligible_registration(self.registration))

    def test_verification_mail_is_scheduled_after_commit_without_cron(self):
        """The same helper used by HTTP routes dispatches a specific mail only."""
        mail_model = type(self.env["mail.mail"])
        with patch.object(mail_model, "send_after_commit", autospec=True) as send_after_commit:
            mail = self.env["mail.mail"].create({
                "subject": "MotoGene - Manage Your Event Registrations",
                "body_html": "<p>Test</p>",
                "email_to": "customer.example@example.com",
                "email_from": "noreply@example.com",
                "auto_delete": False,
            })
            mail.send_after_commit()
            send_after_commit.assert_called_once()
            self.assertEqual(send_after_commit.call_args.args[0].ids, mail.ids)
            self.assertEqual(mail.state, "outgoing")
