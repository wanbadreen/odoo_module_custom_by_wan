# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    @api.model
    def configure_scratch_card_picking_report(self, template_key):
        """Attach to the database's optional Studio copy by its QWeb key."""
        legacy = self.env.ref(
            "motogene_promotion_engine.report_delivery_document_scratch_cards",
            raise_if_not_found=False,
        )
        if legacy:
            legacy.write({"active": False})
        target = self.env["ir.ui.view"].search([
            ("type", "=", "qweb"), ("key", "=", template_key), ("active", "=", True),
        ], limit=1)
        xml_name = "report_picking_copy_scratch_cards"
        extension = self.env.ref(
            "motogene_promotion_engine." + xml_name, raise_if_not_found=False,
        )
        if not target:
            if extension:
                extension.write({"active": False})
            _logger.info("Scratch card print target %s is not present; skipping report extension", template_key)
            return True
        values = {
            "name": "MotoGene scratch cards on Picking Operations copy(4)",
            "type": "qweb",
            "key": "motogene_promotion_engine." + xml_name,
            "inherit_id": target.id,
            "mode": "extension",
            "active": True,
            "arch_db": '''<data>
                <xpath expr="//t[@t-set='seen_product_ids']/following-sibling::table[1]" position="after">
                    <t t-call="motogene_promotion_engine.scratch_card_serial_list"/>
                </xpath>
            </data>''',
        }
        if extension:
            extension.write(values)
        else:
            extension = self.env["ir.ui.view"].create(values)
            self.env["ir.model.data"].create({
                "module": "motogene_promotion_engine", "name": xml_name,
                "model": "ir.ui.view", "res_id": extension.id, "noupdate": True,
            })
        return True

    lucky_draw_entries = fields.Integer(
        related="sale_id.lucky_draw_entries", string="Lucky Draw Entries", readonly=True,
    )
    lucky_draw_program_id = fields.Many2one(
        related="sale_id.lucky_draw_program_id", readonly=True,
    )
    scratch_allocation_deferred = fields.Boolean(related="sale_id.scratch_allocation_deferred", string="Card Allocation Deferred", readonly=True)
    scratch_pending_cards = fields.Integer(related="sale_id.scratch_pending_cards", readonly=True)
    scratch_program_id = fields.Many2one(related="sale_id.scratch_program_id", readonly=True)
    scratch_base_cards = fields.Integer(related="sale_id.scratch_base_cards", readonly=True)
    scratch_vip_cards = fields.Integer(related="sale_id.scratch_vip_cards", readonly=True)
    scratch_total_cards = fields.Integer(
        related="sale_id.scratch_total_cards", string="SO Scratch Cards (Info Only)", readonly=True,
        help="Total entitlement on the Sales Order. When an order has multiple deliveries, "
             "this does not mean the full count should be packed in every delivery.",
    )
    scratch_card_line_ids = fields.One2many(
        "motogene.scratch.picking.card", "picking_id", string="Scratch & Win Cards to Pack",
        copy=False, domain=[("state", "!=", "released")],
    )

    def _scratch_expected_types(self):
        self.ensure_one()
        if self.picking_type_code != "outgoing":
            return []
        return self.sale_id._scratch_order_types() if self.sale_id else []

    @api.model_create_multi
    def create(self, vals_list):
        pickings = super().create(vals_list)
        pickings.filtered(lambda p: p.picking_type_code == "outgoing").mapped("sale_id")._assign_scratch_cards_to_delivery()
        return pickings

    def action_prepare_scratch_cards(self):
        # Compatibility for views from older installed versions during upgrade.
        self.filtered(lambda p: p.picking_type_code == "outgoing").mapped("sale_id")._allocate_scratch_cards()
        return True

    def action_cancel(self):
        for order in self.mapped("sale_id").sorted("id"):
            self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [order.id])
        result = super().action_cancel()
        for picking in self.filtered(lambda p: p.state == "cancel"):
            cards = self.env["motogene.scratch.picking.card"].search([
                ("picking_id", "=", picking.id), ("state", "=", "reserved"),
            ])
            cards.write({"state": "released"})
            picking.invalidate_recordset(["scratch_card_line_ids"])
        return result

    def button_validate(self):
        outgoing = self.filtered(lambda p: p.picking_type_code == "outgoing" and p.state not in ("done", "cancel"))
        outgoing.mapped("sale_id").filtered(lambda order: not order.scratch_allocation_deferred)._allocate_scratch_cards()
        return super().button_validate()

    def _action_done(self):
        for order in self.mapped("sale_id").sorted("id"):
            self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [order.id])
        result = super()._action_done()
        for picking in self.filtered(lambda p: p.picking_type_code == "outgoing" and p.state == "done"):
            picking.scratch_card_line_ids.filtered(lambda c: c.state == "reserved").write({"state": "sent"})
        return result
