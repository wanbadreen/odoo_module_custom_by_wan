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

        amount = self.sale_order_id.apply_loyalty_redemption(
            card=self.loyalty_card_id,
            points=self.points_to_use,
            rm_per_point=self.rm_per_point,
            context_description=_("Redeem %(points).0f loyalty points", points=self.points_to_use),
        )

        if amount <= 0:
            raise UserError(_("Discount amount must be positive."))

        return {'type': 'ir.actions.act_window_close'}
