# -*- coding: utf-8 -*-
"""Protect Activity selection labels while allowing unrelated event editing."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestActivityHistoricalAnswerLabels(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].create({
            "name": "Activity Answer Integrity Test Website",
            "company_id": cls.env.company.id,
        })
        cls.activity = cls.env["motogene.event.activity"].create({
            "name": "Activity Answer Integrity Test",
            "company_id": cls.env.company.id,
            "website_id": cls.website.id,
        })
        cls.source_question = cls.env["event.question"].create({
            "title": "Registration Preference",
            "question_type": "simple_choice",
            "motogene_activity_id": cls.activity.id,
        })
        cls.source_used = cls.env["event.question.answer"].create({
            "name": "Existing Choice",
            "question_id": cls.source_question.id,
        })
        cls.source_unused = cls.env["event.question.answer"].create({
            "name": "Unused Choice",
            "question_id": cls.source_question.id,
        })

        begin = fields.Datetime.to_datetime(fields.Datetime.now()) + timedelta(days=10)
        cls.event = cls.env["event.event"].create({
            "name": "Activity Session for Answer Protection",
            "date_begin": begin,
            "date_end": begin + timedelta(hours=1),
            "company_id": cls.env.company.id,
            "website_id": cls.website.id,
        })
        cls.child_question = cls.env["event.question"].create({
            "title": "Registration Preference",
            "question_type": "simple_choice",
            "event_id": cls.event.id,
            "motogene_activity_source_id": cls.source_question.id,
        })
        cls.child_used = cls.env["event.question.answer"].create({
            "name": "Existing Choice",
            "question_id": cls.child_question.id,
            "motogene_activity_source_answer_id": cls.source_used.id,
        })
        cls.child_unused = cls.env["event.question.answer"].create({
            "name": "Unused Choice",
            "question_id": cls.child_question.id,
            "motogene_activity_source_answer_id": cls.source_unused.id,
        })
        cls.registration = cls.env["event.registration"].create({
            "event_id": cls.event.id,
            "name": "Answer Test Participant",
            "email": "answer-test@example.invalid",
        })
        cls.saved_answer = cls.env["event.registration.answer"].create({
            "registration_id": cls.registration.id,
            "question_id": cls.child_question.id,
            "value_answer_id": cls.child_used.id,
        })

    def test_used_source_answer_cannot_be_renamed(self):
        with self.assertRaises(UserError):
            self.source_used.write({"name": "Changed Choice"})
        self.assertEqual(self.saved_answer.value_answer_id, self.child_used)
        self.assertEqual(self.child_used.name, "Existing Choice")

    def test_used_child_answer_cannot_be_renamed_directly(self):
        with self.assertRaises(UserError):
            self.child_used.write({"name": "Changed Directly"})
        self.assertEqual(self.child_used.name, "Existing Choice")

    def test_unused_choices_are_editable_and_syncable(self):
        self.source_unused.write({"name": "New Choice"})
        self.activity._sync_question_answers(self.source_question, self.child_question)
        self.assertEqual(self.child_unused.name, "New Choice")
        self.assertEqual(self.child_unused.motogene_activity_source_answer_id, self.source_unused)
        # Writing the same label must not block a normal sync.
        self.source_used.write({"name": "Existing Choice"})
        self.activity._sync_question_answers(self.source_question, self.child_question)
        self.assertEqual(self.saved_answer.value_answer_id.name, "Existing Choice")

    def test_unrelated_event_options_remain_editable(self):
        independent_question = self.env["event.question"].create({
            "title": "Independent Event Preference",
            "question_type": "simple_choice",
            "event_id": self.event.id,
        })
        independent = self.env["event.question.answer"].create({
            "name": "Initial", "question_id": independent_question.id,
        })
        independent_answer = self.env["event.registration.answer"].create({
            "registration_id": self.registration.id,
            "question_id": independent_question.id,
            "value_answer_id": independent.id,
        })
        independent.write({"name": "Updated"})
        self.assertEqual(independent.name, "Updated")
        self.assertEqual(independent_answer.value_answer_id.name, "Updated")
