from odoo import fields, models


class MotogeneFedexQuote(models.Model):
    _name = "motogene.fedex.quote"
    _description = "MotoGene FedEx Rate Quote"
    _order = "create_date desc, id desc"

    order_id = fields.Many2one(
        "sale.order", required=True, ondelete="cascade", index=True
    )
    service_type = fields.Char(required=True, readonly=True)
    service_name = fields.Char(required=True, readonly=True)
    amount = fields.Float(readonly=True, digits=(16, 2))
    currency_code = fields.Char(readonly=True)
    surcharge = fields.Float(readonly=True, digits=(16, 2))
    ship_date = fields.Date(readonly=True)
    delivery_date = fields.Date(readonly=True)
    transit_message = fields.Char(readonly=True)
    destination_country_id = fields.Many2one("res.country", readonly=True)
    estimated = fields.Boolean(default=True, readonly=True)
