from odoo import api, fields, models, _
from odoo.exceptions import UserError

class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_open_loyalty_redeem_wizard(self):
        self.ensure_one()
        program = self.env["loyalty.program"].search([
            ("program_type", "=", "loyalty"),
            ("active", "=", True),
        ], limit=1)

        if not program:
            raise UserError(_("No active loyalty program found."))

        card = self.env["loyalty.card"].search([
            ("program_id", "=", program.id),
            ("partner_id", "=", self.partner_id.id),
        ], limit=1)

        if not card or card.points <= 0:
            raise UserError(_("This customer has no loyalty points."))

        return {
            "name": _("Redeem Loyalty Points"),
            "type": "ir.actions.act_window",
            "res_model": "loyalty.partial.redeem.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_sale_order_id": self.id,
                "default_loyalty_card_id": card.id,
                "default_available_points": card.points,
            }
        }