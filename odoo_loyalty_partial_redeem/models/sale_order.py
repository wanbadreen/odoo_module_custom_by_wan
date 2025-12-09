from odoo import _, models
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
            },
        }

    def _get_loyalty_discount_product(self):
        self.ensure_one()
        discount_product = self.env['product.product'].search([
            ('default_code', '=', 'Loyalty Point Redemption'),
        ], limit=1)
        if not discount_product:
            discount_product = self.env['product.product'].search([
                ('name', '=', 'loyalty point redemption'),
            ], limit=1)

        if not discount_product:
            raise UserError(
                _(
                    "Product 'loyalty point redemption' not found. "
                    "Please create it or adjust the default_code in the wizard."
                )
            )
        return discount_product

    def apply_loyalty_redemption(self, card, points, rm_per_point=0.01, context_description=None):
        self.ensure_one()
        if not card:
            raise UserError(_("No loyalty card found for this customer."))

        points_to_use = points or 0.0
        if points_to_use <= 0:
            raise UserError(_("Points to use must be greater than zero."))

        available_points = card.points or 0.0
        if points_to_use > available_points:
            raise UserError(_("You cannot use more points than available."))

        rate = rm_per_point or 0.0
        amount = points_to_use * rate
        if amount <= 0:
            raise UserError(_("Discount amount must be positive."))

        discount_product = self._get_loyalty_discount_product()
        redeem_label = context_description or _("Redeem %(points).0f loyalty points", points=points_to_use)

        self.env['sale.order.line'].create({
            'order_id': self.id,
            'product_id': discount_product.id,
            'name': redeem_label,
            'product_uom_qty': 1.0,
            'price_unit': -amount,
        })

        card.sudo().points = available_points - points_to_use

        self.env['loyalty.history'].sudo().create({
            'card_id': card.id,
            'description': _(
                "Redeem %(points).0f pts on order %(order)s",
                points=points_to_use,
                order=self.name,
            ),
            'issued': 0.0,
            'used': points_to_use,
            'order_id': self.id,
            'order_model': 'sale.order',
        })

        return amount
