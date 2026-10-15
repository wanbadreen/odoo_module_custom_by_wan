# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class MotoGeneEventContactConfig(models.Model):
    _name = "motogene.event.contact.config"
    _description = "MotoGene Event Contacts Settings"

    name = fields.Char(default="MotoGene Event Contacts", required=True)
    website_id = fields.Many2one("website", required=True, ondelete="restrict")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    enabled = fields.Boolean(default=False,
        help="Disabled by default. Enable only after reviewing the event registration flow on staging.")
    review_user_id = fields.Many2one("res.users", string="Identity Review Assignee",
        help="Schedule an Odoo To Do activity for new identity conflicts; no email is sent by this feature.")
    _sql_constraints = [
        ("website_unique", "unique(website_id)", "Only one Contacts configuration per website."),
    ]

    @api.constrains("company_id", "website_id")
    def _check_company(self):
        for record in self:
            if record.website_id.company_id and record.website_id.company_id != record.company_id:
                raise ValidationError(_("The Contacts settings company must match the website company."))

    @api.model
    def for_event(self, event):
        """Opt in twice: event AND site. Never affect unrelated Single Events."""
        if not event or not event.motogene_contacts_enabled or not event.website_id:
            return self.browse()
        return self.sudo().search([
            ("enabled", "=", True),
            ("website_id", "=", event.website_id.id),
            ("company_id", "=", event.company_id.id),
        ], limit=1)
