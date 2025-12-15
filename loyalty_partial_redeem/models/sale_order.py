import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    has_loyalty_redeem = fields.Boolean(
        string="Has Loyalty Redemption",
        compute="_compute_has_loyalty_redeem",
        store=True,
    )
    loyalty_points_redeemed = fields.Float(string="Loyalty Points Redeemed", default=0.0)
    loyalty_card_id = fields.Many2one("loyalty.card", string="Loyalty Card")
    loyalty_redeem_reversed = fields.Boolean(string="Loyalty Redemption Reversed", default=False)

    @api.depends("order_line", "order_line.is_loyalty_redeem_line", "order_line.product_id")
    def _compute_has_loyalty_redeem(self):
        for order in self:
            order.has_loyalty_redeem = bool(order._get_loyalty_redeem_lines())

    @staticmethod
    def _is_redeem_line(line):
        default_code = (line.product_id.default_code or "").strip().lower()
        product_name = (line.product_id.name or "").strip().lower()
        return bool(
            line.is_loyalty_redeem_line
            or default_code == "loyalty point redemption"
            or product_name in {"loyalty point redemption", "loyalty points redemption"}
        )

    def _get_loyalty_redeem_lines(self):
        self.ensure_one()
        return self.order_line.filtered(self._is_redeem_line)

    def _get_redeemed_points_value(self):
        self.ensure_one()
        if self.loyalty_points_redeemed:
            return self.loyalty_points_redeemed
        history = self.env["loyalty.history"].search([
            ("order_id", "=", self.id),
            ("order_model", "=", "sale.order"),
        ])
        return sum(history.mapped("used"))

    def _find_loyalty_card_from_history(self):
        self.ensure_one()
        history = self.env["loyalty.history"].search([
            ("order_id", "=", self.id),
            ("order_model", "=", "sale.order"),
        ], limit=1)
        return history.card_id

    def action_open_loyalty_redeem_wizard(self):
        self.ensure_one()
        if self._get_loyalty_redeem_lines():
            raise UserError(_("This order already has a loyalty redemption. Remove the redemption line first."))
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

    def action_cancel(self):
        res = super().action_cancel()
        self._reverse_loyalty_points_on_cancel()
        return res

    def _reverse_loyalty_points_on_cancel(self):
        for order in self:
            redeem_lines = order._get_loyalty_redeem_lines()
            if not redeem_lines or order.loyalty_redeem_reversed:
                continue
            points = order._get_redeemed_points_value()
            if points <= 0:
                continue
            card = order.loyalty_card_id or order._find_loyalty_card_from_history()
            if not card:
                _logger.warning("Cannot reverse loyalty redemption for order %s: no card found", order.name)
                continue
            card.points += points
            self.env["loyalty.history"].create({
                "card_id": card.id,
                "description": _("Reverse Redemption (SO Cancelled): %s") % order.name,
                "issued": points,
                "used": 0.0,
                "order_id": order.id,
                "order_model": "sale.order",
            })
            order.loyalty_redeem_reversed = True
            # TODO: Reverse redemption when a refund/return is confirmed, hooking into the
            # related accounting move or return validation workflow.

