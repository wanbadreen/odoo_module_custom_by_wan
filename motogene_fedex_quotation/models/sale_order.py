from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    fedex_quote_ids = fields.One2many(
        "motogene.fedex.quote", "order_id", string="FedEx Rate Quotations",
        readonly=True,
    )

    def action_open_fedex_quote_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "FedEx Shipping Quotation",
            "res_model": "motogene.fedex.quote.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_order_id": self.id,
            },
        }
