from odoo import api, fields, models, _
from odoo.exceptions import UserError

class LoyaltyPartialRedeemWizard(models.TransientModel):
    _name = 'loyalty.partial.redeem.wizard'
    _description = 'Partial Loyalty Redemption'

    sale_order_id = fields.Many2one('sale.order', required=True, readonly=True)
    loyalty_card_id = fields.Many2one('loyalty.card', required=True, readonly=True)
    available_points = fields.Float(readonly=True)
    points_to_use = fields.Float(required=True)
    rm_per_point = fields.Float(required=True, default=0.01)
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id.id, readonly=True)
    amount_discount = fields.Monetary(currency_field='currency_id', compute='_compute_amount_discount', readonly=True)

    @api.depends('points_to_use', 'rm_per_point')
    def _compute_amount_discount(self):
        for wiz in self:
            wiz.amount_discount = max(wiz.points_to_use, 0.0) * max(wiz.rm_per_point, 0.0)

    def action_confirm(self):
        self.ensure_one()
        if self.points_to_use <= 0:
            raise UserError(_("Points to use must be greater than zero."))
        if self.points_to_use > self.available_points:
            raise UserError(_("You cannot use more points than available."))

        amount = self.amount_discount
        if amount <= 0:
            raise UserError(_("Discount amount must be positive."))

        order = self.sale_order_id
        card = self.loyalty_card_id

        discount_product = self.env['product.product'].search([
            ('default_code', '=', 'Loyalty Point Redemption'),
        ], limit=1)

        if not discount_product:
            discount_product = self.env['product.product'].search([
                ('name', '=', 'loyalty point redemption'),
            ], limit=1)

        if not discount_product:
            raise UserError("Product 'loyalty point redemption' not found.")

        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': discount_product.id,
            'name': f"Redeem {self.points_to_use:.0f} loyalty points",
            'product_uom_qty': 1.0,
            'price_unit': -amount,
        })

        card.points -= self.points_to_use

        self.env['loyalty.history'].create({
            'card_id': card.id,
            'description': f"Redeem {self.points_to_use:.0f} pts on order {order.name}",
            'issued': 0.0,
            'used': self.points_to_use,
            'order_id': order.id,
            'order_model': 'sale.order',
        })

        return {'type': 'ir.actions.act_window_close'}
