from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    motogene_shopee_preview_id = fields.Many2one(
        "motogene.shopee.import.order",
        string="MotoGene Shopee Preview",
        copy=False, readonly=True, index=True,
    )
    _sql_constraints = [
        (
            "motogene_shopee_preview_unique",
            "unique(motogene_shopee_preview_id)",
            "A Shopee order preview can be linked to only one Sales Order.",
        ),
    ]
