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
    scratch_program_id = fields.Many2one(related="sale_id.scratch_program_id", readonly=True)
    scratch_base_cards = fields.Integer(related="sale_id.scratch_base_cards", readonly=True)
    scratch_vip_cards = fields.Integer(related="sale_id.scratch_vip_cards", readonly=True)
    scratch_total_cards = fields.Integer(
        related="sale_id.scratch_total_cards", string="SO Scratch Cards (Info Only)", readonly=True,
        help="Total entitlement on the Sales Order. When an order has multiple deliveries, "
             "this does not mean the full count should be packed in every delivery.",
    )
