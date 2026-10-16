# -*- coding: utf-8 -*-
from odoo import fields, models, _
from odoo.exceptions import UserError


class ScratchShortageWizard(models.TransientModel):
    _name = "motogene.scratch.shortage.wizard"
    _description = "Confirm Sales Order with Scratch Card Shortage"

    order_ids = fields.Many2many("sale.order", required=True, readonly=True)
    message = fields.Text(readonly=True)

    def action_proceed_without_cards(self):
        return self._proceed("without")

    def action_proceed_with_available_cards(self):
        return self._proceed("available")

    def _proceed(self, mode):
        self.ensure_one()
        orders = self.order_ids.exists()
        if not orders or any(order.state not in ("draft", "sent") for order in orders):
            raise UserError(_("The quotation has changed. Close this warning and confirm it again."))
        # Revalidate everything; only genuine pool exhaustion can be deferred.
        orders.with_context(
            motogene_allow_scratch_shortage=True,
            motogene_scratch_shortage_mode=mode,
        ).action_confirm()
        return {"type": "ir.actions.act_window_close"}
