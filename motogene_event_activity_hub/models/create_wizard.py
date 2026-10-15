# -*- coding: utf-8 -*-
from odoo import fields, models, _


class MotoGeneEventCreationWizard(models.TransientModel):
    _name = "motogene.event.create.wizard"
    _description = "Choose Event Creation Type"

    creation_type = fields.Selection(
        [("single", "Single Event"), ("multiple", "Multiple Sessions")],
        required=True, default="single",
        help="Choose between one standalone event or an activity containing multiple sessions.")

    def action_continue(self):
        self.ensure_one()
        if self.creation_type == "single":
            return {
                "type": "ir.actions.act_window",
                "name": _("New Event"),
                "res_model": "event.event",
                "view_mode": "form",
                "views": [(False, "form")],
                "target": "current",
                "context": {"default_company_id": self.env.company.id},
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("New Multiple Sessions Activity"),
            "res_model": "motogene.event.activity",
            "view_mode": "form",
            "views": [(self.env.ref("motogene_event_activity_hub.view_activity_form").id, "form")],
            "target": "current",
            "context": {"default_company_id": self.env.company.id},
        }
