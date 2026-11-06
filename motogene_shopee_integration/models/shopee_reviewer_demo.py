from odoo import fields, models


class ShopeeReviewerDemo(models.Model):
    _name = "motogene.shopee.reviewer.demo"
    _description = "Shopee Integration Reviewer Demo (Fictitious Data)"
    _order = "id desc"

    name = fields.Char(string="Sample Shopee Order", required=True, readonly=True)
    workflow_stage = fields.Selection(
        [
            ("preview", "Order Preview"),
            ("mapped", "Product Mapped"),
            ("sale", "Sales Order Prepared"),
            ("shipment", "Shipment Prepared"),
            ("awb", "AWB Ready"),
        ],
        string="Demo Workflow Stage",
        readonly=True,
    )
    item_name = fields.Char(string="Sample Product", readonly=True)
    quantity = fields.Integer(string="Quantity", readonly=True)
    total_amount = fields.Float(string="Sample Order Total (MYR)", readonly=True)
    shipping_method = fields.Char(string="Shipping Method", readonly=True)
    notes = fields.Text(string="Demo Explanation", readonly=True)
