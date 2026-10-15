# -*- coding: utf-8 -*-
"""No live SMTP, Odoo.sh production, or global scheduled actions in these tests."""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase


class TestMotoGeneEventContacts(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].create({
            "name": "MotoGene Contact Tests",
            "company_id": cls.env.company.id,
        })
        cls.config = cls.env["motogene.event.contact.config"].create({
            "name": "Test Contact Integration",
            "company_id": cls.env.company.id,
            "website_id": cls.website.id,
            "enabled": True,
        })
        cls.self_service = cls.env["motogene.event.self.service.config"].create({
            "name": "Test Booking Access",
            "website_id": cls.website.id,
            "company_id": cls.env.company.id,
            "enabled": True,
        })
        begin = fields.Datetime.to_datetime(fields.Datetime.now()) + timedelta(days=5)
        cls.event = cls.env["event.event"].create({
            "name": "Contact Integration Test",
            "company_id": cls.env.company.id,
            "website_id": cls.website.id,
            "date_begin": begin,
            "date_end": begin + timedelta(hours=2),
            "motogene_contacts_enabled": True,
        })
        cls.tag = cls.env["res.partner.category"].create({"name": "Contact Tests - Event"})
        cls.event.motogene_contact_tag_ids = [(6, 0, cls.tag.ids)]

    def _register(self, name, email=False):
        return self.env["event.registration"].create({
            "event_id": self.event.id,
            "name": name,
            "email": email,
        })

    def test_new_contact_reuses_existing_and_keeps_tags(self):
        vip = self.env["res.partner.category"].create({"name": "Contact Test VIP"})
        existing = self.env["res.partner"].create({
            "name": "Aina Ahmad",
            "email": "aina@example.invalid",
            "company_id": self.env.company.id,
            "category_id": [(4, vip.id)],
        })
        r1 = self._register("Aina Ahmad", "AINA@example.invalid")
        r2 = self._register(" Aina   Ahmad ", "aina@example.invalid")
        self.assertEqual(r1.motogene_attendee_contact_id, existing)
        self.assertEqual(r2.motogene_attendee_contact_id, existing)
        self.assertEqual(r1.motogene_contact_sync_status, "matched")
        self.assertIn(vip, existing.category_id)
        self.assertIn(self.tag, existing.category_id)

    def test_new_contact_created_once_for_same_identity(self):
        email = "unique-event-contact@example.invalid"
        first = self._register("Event New Participant", email)
        second = self._register("Event New Participant", email)
        self.assertEqual(first.motogene_contact_sync_status, "created")
        self.assertEqual(second.motogene_contact_sync_status, "matched")
        self.assertEqual(first.motogene_attendee_contact_id, second.motogene_attendee_contact_id)
        self.assertIn(self.tag, first.motogene_attendee_contact_id.category_id)

    def test_different_name_same_email_goes_to_review(self):
        contact = self.env["res.partner"].create({
            "name": "Existing Customer",
            "email": "identity-conflict@example.invalid",
            "company_id": self.env.company.id,
        })
        attendee = self._register("Different IC Name", "identity-conflict@example.invalid")
        self.assertEqual(attendee.motogene_contact_sync_status, "review")
        self.assertFalse(attendee.motogene_attendee_contact_id)
        review = attendee.motogene_contact_review_ids
        self.assertEqual(review.state, "pending")
        self.assertIn(contact, review.candidate_ids)
        review.chosen_partner_id = contact
        review.action_link_existing()
        self.assertEqual(attendee.motogene_attendee_contact_id, contact)
        self.assertEqual(review.state, "resolved")

    def test_two_existing_contacts_with_email_are_not_auto_merged(self):
        email = "duplicate-event-contacts@example.invalid"
        for suffix in ("First", "Second"):
            self.env["res.partner"].create({
                "name": "Same IC Name " + suffix,
                "email": email,
                "company_id": self.env.company.id,
            })
        attendee = self._register("Same IC Name First", email)
        self.assertEqual(attendee.motogene_contact_sync_status, "review")
        self.assertEqual(attendee.motogene_contact_review_ids.reason, "multiple_contacts")

    def test_booking_owner_can_find_all_and_participant_only_own(self):
        owner = "event-booking-owner@example.invalid"
        r1 = self._register("Farah", "farah-own@example.invalid")
        r2 = self._register("Aina")
        r3 = self._register("Riena", "riena-own@example.invalid")
        booking = self.env["motogene.event.booking"].create({
            "event_id": self.event.id,
            "website_id": self.website.id,
            "company_id": self.env.company.id,
            "owner_email": owner,
        })
        (r1 | r2 | r3).write({"motogene_booking_id": booking.id})
        self.assertEqual(r2.motogene_contact_sync_status, "no_email")
        self.assertFalse(r2.motogene_attendee_contact_id)
        self.assertEqual(
            set(self.self_service._registrations_for_email(owner).ids),
            set((r1 | r2 | r3).ids),
        )
        self.assertIn(r1, self.self_service._registrations_for_email("farah-own@example.invalid"))
        self.assertNotIn(r2, self.self_service._registrations_for_email("farah-own@example.invalid"))
        self.assertIn(r3, self.self_service._registrations_for_email("riena-own@example.invalid"))
        self.assertNotIn(r1, self.self_service._registrations_for_email("riena-own@example.invalid"))

    def test_owner_cancel_one_registration_not_others(self):
        owner = "cancel-booking-owner@example.invalid"
        registration = self._register("Aina for Cancel")
        other = self._register("Riena for Keep")
        booking = self.env["motogene.event.booking"].create({
            "event_id": self.event.id,
            "company_id": self.env.company.id,
            "website_id": self.website.id,
            "owner_email": owner,
        })
        (registration | other).write({"motogene_booking_id": booking.id})
        mail_model = type(self.env["mail.mail"])
        with patch.object(mail_model, "send_after_commit", autospec=True) as mail_send:
            self.self_service._cancel_for_verified_email(registration.id, owner)
            mail_send.assert_called_once()
        self.assertEqual(registration.state, "cancel")
        self.assertNotEqual(other.state, "cancel")

    def test_disabled_config_does_not_create_contact(self):
        self.config.enabled = False
        record = self._register("Disabled User", "disabled-event-contact@example.invalid")
        self.assertEqual(record.motogene_contact_sync_status, "off")
        self.assertFalse(record.motogene_attendee_contact_id)
        self.config.enabled = True
