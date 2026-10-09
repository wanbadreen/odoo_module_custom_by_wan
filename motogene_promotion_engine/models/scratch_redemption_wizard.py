# -*- coding: utf-8 -*-
from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError
from .scratch_redemption import scratch_pool_search_domain


class ScratchRedemptionWizard(models.TransientModel):
    _name = "motogene.scratch.redemption.wizard"
    _description = "Redeem Scratch Card on Quotation"

    sale_id = fields.Many2one("sale.order", required=True, readonly=True)
    available_card_ids = fields.Many2many("motogene.scratch.picking.card", compute="_compute_available_cards")
    card_id = fields.Many2one("motogene.scratch.picking.card", required=True, string="Card Serial")
    expiry_date = fields.Date(related="card_id.expiry_date", string="Card Redemption Expiry")
    pool_id = fields.Many2one("motogene.scratch.serial.pool", compute="_compute_pool", search="_search_pool")
    reward_type = fields.Selection(related="pool_id.redemption_reward_type")
    prize_description = fields.Char(related="pool_id.prize_description", string="Prize")
    mystery_product_id = fields.Many2one("product.product", string="Mystery Gift Product", domain=[("sale_ok", "=", True)])
    card_photo = fields.Binary(required=True, string="Scratched Card Photo")
    photo_filename = fields.Char()
    notes = fields.Text(string="CS Verification Notes")

    @api.depends("sale_id", "sale_id.partner_id", "sale_id.company_id", "sale_id.state")
    def _compute_available_cards(self):
        for wizard in self:
            wizard.available_card_ids = wizard.sale_id._redeemable_scratch_cards() if wizard.sale_id else False

    @api.depends("card_id", "card_id.prefix", "card_id.program_id.scratch_serial_pool_ids.prefix")
    def _compute_pool(self):
        for wizard in self:
            wizard.pool_id = wizard.card_id.program_id.scratch_serial_pool_ids.filtered(lambda p: p.prefix == wizard.card_id.prefix)[:1]

    @api.model
    def _search_pool(self, operator, value):
        return scratch_pool_search_domain(self.env, operator, value)

    @api.onchange("card_id")
    def _onchange_card(self):
        self.mystery_product_id = False
        self.card_photo = False
        self.photo_filename = False

    def action_confirm(self):
        self.ensure_one()
        self.sale_id.check_access_rights("write")
        self.sale_id.check_access_rule("write")
        if self.card_id not in self.sale_id._redeemable_scratch_cards():
            raise UserError(_("This card is no longer eligible for this customer's quotation."))
        record = self.env["motogene.scratch.redemption"].create({
            "card_id": self.card_id.id, "target_sale_id": self.sale_id.id,
            "card_photo": self.card_photo, "photo_filename": self.photo_filename,
            "notes": self.notes, "mystery_product_id": self.mystery_product_id.id,
            "gift_choice_ids": [Command.create({
                "reward_line_id": line.reward_line_id.id, "product_id": line.product_id.id,
            }) for line in self.gift_choice_ids],
        })
        record.action_confirm_redemption()
        return {"type": "ir.actions.act_window_close"}

