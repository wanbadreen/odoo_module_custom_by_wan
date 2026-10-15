# -*- coding: utf-8 -*-
from odoo import api, fields, models


class EventEvent(models.Model):
    _inherit = "event.event"

    motogene_contacts_enabled = fields.Boolean(
        string="Enable Attendee Contacts", default=False,
        help="Collect one Booking Email and optional individual participant emails when the website Contacts configuration is enabled.")
    motogene_contact_tag_ids = fields.Many2many(
        "res.partner.category", "motogene_event_contact_tag_rel",
        "event_id", "tag_id", string="Attendee Contact Tags",
        help="Only added to Contacts after a confirmed identity match or new email-based Contact creation.")
    motogene_pending_review_count = fields.Integer(compute="_compute_motogene_review_count")

    def _compute_motogene_review_count(self):
        grouped = self.env["motogene.event.contact.review"].read_group(
            [("event_id", "in", self.ids), ("state", "=", "pending")],
            ["event_id"], ["event_id"],
        ) if self.ids else []
        counts = {line["event_id"][0]: line["event_id_count"] for line in grouped}
        for event in self:
            event.motogene_pending_review_count = counts.get(event.id, 0)

    def action_motogene_contact_reviews(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Contact Reviews",
            "res_model": "motogene.event.contact.review",
            "view_mode": "list,form",
            "domain": [("event_id", "=", self.id)],
            "context": {"search_default_pending": 1},
        }


class MotoGeneActivity(models.Model):
    _inherit = "motogene.event.activity"

    motogene_contacts_enabled = fields.Boolean(default=False, string="Enable Attendee Contacts",
        help="Share one Booking Email per registration and link individual attendees to Contacts.")
    motogene_contact_tag_ids = fields.Many2many(
        "res.partner.category", "motogene_activity_contact_tag_rel",
        "activity_id", "tag_id", string="Attendee Contact Tags")
    motogene_pending_review_count = fields.Integer(compute="_compute_motogene_review_count")

    def _compute_motogene_review_count(self):
        for activity in self:
            activity.motogene_pending_review_count = self.env["motogene.event.contact.review"].search_count([
                ("event_id", "in", activity.session_ids.mapped("event_id").ids),
                ("state", "=", "pending"),
            ])

    def action_prepare_sessions(self):
        result = super().action_prepare_sessions()
        for activity in self:
            activity.session_ids.mapped("event_id").write({
                "motogene_contacts_enabled": activity.motogene_contacts_enabled,
                "motogene_contact_tag_ids": [(6, 0, activity.motogene_contact_tag_ids.ids)],
            })
        return result

    def _validate_activity_questions(self):
        """Allow an optional attendee Email only with a verified booking-email flow."""
        for activity in self:
            enabled = bool(activity.motogene_contacts_enabled and
                           self.env["motogene.event.contact.config"].sudo().search([
                               ("enabled", "=", True),
                               ("website_id", "=", activity.website_id.id),
                               ("company_id", "=", activity.company_id.id),
                           ], limit=1))
            if not enabled:
                super(MotoGeneActivity, activity)._validate_activity_questions()
                continue
            questions = activity.question_ids
            name = questions.filtered(lambda q: q.question_type == "name")
            email = questions.filtered(lambda q: q.question_type == "email")
            if len(name) != 1 or not name.is_mandatory_answer or name.once_per_order or len(email) != 1 or email.once_per_order:
                from odoo.exceptions import ValidationError
                from odoo import _
                raise ValidationError(_("One mandatory Name and one per-attendee Email question are required. Participant Email may be optional."))
            for kind in ("phone", "company_name"):
                if len(questions.filtered(lambda q: q.question_type == kind)) > 1:
                    from odoo.exceptions import ValidationError
                    raise ValidationError("Only one Phone or Company question may be used.")
            if any(q.question_type == "simple_choice" and not q.answer_ids for q in questions):
                from odoo.exceptions import ValidationError
                raise ValidationError("A Selection question needs at least one answer.")

    def action_motogene_contact_reviews(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Contact Reviews",
            "res_model": "motogene.event.contact.review",
            "view_mode": "list,form",
            "domain": [("event_id", "in", self.session_ids.mapped("event_id").ids)],
            "context": {"search_default_pending": 1},
        }
