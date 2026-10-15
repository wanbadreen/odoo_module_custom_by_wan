# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MotoGeneContactReview(models.Model):
    _name = "motogene.event.contact.review"
    _description = "MotoGene Event Contact Identity Review"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    registration_id = fields.Many2one("event.registration", required=True,
        readonly=True, index=True, ondelete="cascade")
    event_id = fields.Many2one("event.event", required=True, readonly=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", required=True, readonly=True)
    name = fields.Char(related="registration_id.name", string="Participant")
    email = fields.Char(related="registration_id.email", string="Participant Email")
    reason = fields.Selection([
        ("name_mismatch", "Email Match / Name Mismatch"),
        ("multiple_contacts", "Multiple Contacts Share Email"),
        ("invalid_identity", "Invalid Participant Identity"),
    ], readonly=True, required=True)
    state = fields.Selection([("pending", "Pending"), ("resolved", "Resolved")],
        default="pending", tracking=True, index=True, required=True)
    candidate_ids = fields.Many2many(
        "res.partner", "motogene_contact_review_candidate_rel",
        "review_id", "partner_id", readonly=True, string="Existing Candidates")
    chosen_partner_id = fields.Many2one("res.partner", string="Link Existing Contact")
    resolved_by_id = fields.Many2one("res.users", readonly=True)
    resolved_at = fields.Datetime(readonly=True)
    resolution_note = fields.Text(string="Staff Review Notes")

    _sql_constraints = [
        ("unique_registration_review", "unique(registration_id)",
         "Only one Contact review record is allowed per attendee."),
    ]

    def _finish(self, partner, status):
        self.ensure_one()
        if self.state != "pending":
            raise UserError(_("This review has already been resolved."))
        self.registration_id._motogene_link_contact(partner, status)
        self.write({
            "state": "resolved",
            "resolved_by_id": self.env.user.id,
            "resolved_at": fields.Datetime.now(),
        })
        self.activity_ids.action_feedback(feedback="Contact review resolved.")

    def action_link_existing(self):
        for review in self:
            partner = review.chosen_partner_id
            if not partner or partner not in review.candidate_ids:
                raise UserError(_("Select a Contact from the review candidate list."))
            if partner.company_id and partner.company_id != review.company_id:
                raise UserError(_("The selected Contact belongs to a different company."))
            review._finish(partner, "matched")
        return True

    def action_create_separate(self):
        for review in self:
            reg = review.registration_id
            if not (reg.name or "").strip():
                raise UserError(_("A participant name is required to create a Contact."))
            partner = self.env["res.partner"].sudo().create({
                "name": reg.name.strip(),
                "email": reg.email or False,
                "phone": reg.phone or False,
                "company_id": review.company_id.id,
                "company_type": "person",
            })
            review._finish(partner, "created")
        return True
