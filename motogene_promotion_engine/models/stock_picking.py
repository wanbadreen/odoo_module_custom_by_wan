# -*- coding: utf-8 -*-
from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    lucky_draw_entries = fields.Integer(
        related="sale_id.lucky_draw_entries", string="Lucky Draw Entries", readonly=True,
    )
    lucky_draw_program_id = fields.Many2one(
        related="sale_id.lucky_draw_program_id", readonly=True,
    )
