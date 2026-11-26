from odoo import fields, models

class Product(models.Model):
    _inherit = 'product.product'
    morimoto_mobile_enabled = fields.Boolean(default=False, copy=False)

class SaleOrder(models.Model):
    _inherit = 'sale.order'
    morimoto_mobile_key = fields.Char(copy=False, index=True)
    morimoto_mobile_fingerprint = fields.Char(copy=False)
    _morimoto_mobile_key_unique = models.Constraint("unique(morimoto_mobile_key)", "Mobile request already exists.")
