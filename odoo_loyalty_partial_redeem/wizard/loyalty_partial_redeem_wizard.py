from odoo import api, fields, models, _
from odoo.exceptions import UserError


class LoyaltyPartialRedeemWizard(models.TransientModel):
    _name = 'loyalty.partial.redeem.wizard'
    _description = 'Partial Loyalty Redemption'

    sale_order_id = fields.Many2one(
        'sale.order',
        string="Sale Order",
        required=True,
        readonly=True,
    )
    loyalty_card_id = fields.Many2one(
        'loyalty.card',
        string="Loyalty Card",
        required=True,
        readonly=True,
    )

    available_points = fields.Float(
        string="Available Points",
        readonly=True,
    )
    points_to_use = fields.Float(
        string="Points to Redeem",
        required=True,
        help="Number of points the customer wants to redeem.",
    )

    rm_per_point = fields.Float(
        string="RM per point",
        required=True,
        default=0.01,  # 1 point = RM0.01
    )

    currency_id = fields.Many2one(
        'res.currency',
        string="Currency",
        default=lambda self: self.env.company.currency_id.id,
        readonly=True,
    )

    amount_discount = fields.Monetary(
        string="Discount Amount (RM)",
        currency_field='currency_id',
        compute='_compute_amount_discount',
        store=False,
        readonly=True,
    )

    @api.depends('points_to_use', 'rm_per_point')
    def _compute_amount_discount(self):
        for wiz in self:
            pts = wiz.points_to_use or 0.0
            rate = wiz.rm_per_point or 0.0
            wiz.amount_discount = max(pts, 0.0) * max(rate, 0.0)

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

        # 1) Cari product "loyalty point redemption"
        discount_product = self.env['product.product'].search([
            ('default_code', '=', 'Loyalty Point Redemption'),
        ], limit=1)

        if not discount_product:
            discount_product = self.env['product.product'].search([
                ('name', '=', 'loyalty point redemption'),
            ], limit=1)

        if not discount_product:
            raise UserError(
                "Product 'loyalty point redemption' not found. "
                "Please create it or adjust the default_code in the wizard."
            )

        # 2) Create line diskaun dalam quotation
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': discount_product.id,
            'name': f"Redeem {self.points_to_use:.0f} loyalty points",
            'product_uom_qty': 1.0,
            'price_unit': -amount,  # negative = diskaun
        })

        # 3) Tolak point dalam loyalty card
        # NOTE: kalau nama field balance lain dari 'points', tukar sini
        card.points = (card.points or 0.0) - self.points_to_use

        # 4) Rekodkan penggunaan dalam history (supaya 'Used' update)
        self.env['loyalty.history'].create({
            'card_id': card.id,
            'description': f"Redeem {self.points_to_use:.0f} pts on order {order.name}",
            'issued': 0.0,
            'used': self.points_to_use,
            'order_id': order.id,
            'order_model': 'sale.order',
        })

        return {'type': 'ir.actions.act_window_close'}

