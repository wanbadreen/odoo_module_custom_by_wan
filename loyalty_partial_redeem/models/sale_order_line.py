from odoo import fields, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    is_loyalty_redeem_line = fields.Boolean(
        string="Loyalty Redemption Line",
        default=False,
        index=True,
    )
