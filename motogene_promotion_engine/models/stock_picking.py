# -*- coding: utf-8 -*-
import math
from collections import Counter

from odoo import fields, models, _
from odoo.exceptions import UserError


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
    scratch_card_line_ids = fields.One2many(
        "motogene.scratch.picking.card", "picking_id", string="Scratch & Win Cards to Pack",
        copy=False,
    )

    def _scratch_expected_types(self):
        """Cards on this delivery; unmatched spend cards go to the first delivery."""
        self.ensure_one()
        program = self.sale_id.scratch_program_id
        if not program or not self.sale_id.scratch_total_cards or self.picking_type_code != "outgoing":
            return []
        packages = {line.product_tmpl_id.id: line for line in program.scratch_package_line_ids}
        types = []
        vip = program._is_scratch_vip_customer(self.sale_id)
        for move in self.move_ids.filtered(lambda m: m.state != "cancel"):
            package = packages.get(move.product_id.product_tmpl_id.id)
            if not package:
                continue
            unit_count = math.floor(float(move.product_uom_qty or 0) + 1e-9)
            configured = [p.strip().upper() for p in (package.card_prefixes or "").split(",") if p.strip()]
            if not configured:
                raise UserError(_("Set the card types for package %s in the promotion first.") % package.product_tmpl_id.display_name)
            types.extend(configured * unit_count)
            if vip:
                types.extend([program.scratch_vip_prefix] * unit_count)

        # The advertised package cards are already included in spend-based entitlement.
        # Allocate any balance from other spending exactly once across deliveries.
        included = sum(
            len([p for p in (package.card_prefixes or "").split(",") if p.strip()])
            * math.floor(float(line.product_uom_qty or 0) + 1e-9)
            for line in self.sale_id.order_line
            for package in [packages.get(line.product_id.product_tmpl_id.id)]
            if package and line.product_uom_qty > 0 and program._is_normal_paid_line(line)
        )
        extra = self.sale_id.scratch_base_cards - included
        if extra < 0:
            raise UserError(_("Package card allocations exceed the order's spend-based card entitlement."))
        if extra:
            earlier = self.sale_id.picking_ids.filtered(
                lambda p: p.id != self.id and p.picking_type_code == "outgoing"
                and p.state != "cancel" and p.scratch_card_line_ids
            )
            if not earlier and self.id == min(
                self.sale_id.picking_ids.filtered(
                    lambda p: p.picking_type_code == "outgoing" and p.state != "cancel"
                ).ids or [self.id]
            ):
                types.extend([program.scratch_extra_prefix] * extra)
        return types

    def action_prepare_scratch_cards(self):
        for picking in self:
            if picking.state in ("done", "cancel"):
                raise UserError(_("Scratch cards can only be prepared before delivery validation."))
            expected = picking._scratch_expected_types()
            if picking.scratch_card_line_ids:
                if Counter(picking.scratch_card_line_ids.mapped("prefix")) != Counter(expected):
                    raise UserError(_("The delivery changed after cards were assigned. Review the allocation before proceeding."))
                continue
            pools = {pool.prefix: pool for pool in picking.sale_id.scratch_program_id.scratch_serial_pool_ids}
            for prefix in expected:
                if prefix not in pools:
                    raise UserError(_("Configure serial range for card type %s first.") % prefix)
                self.env["motogene.scratch.picking.card"].create({
                    "picking_id": picking.id,
                    "program_id": picking.sale_id.scratch_program_id.id,
                    "prefix": prefix,
                    "serial_number": pools[prefix].reserve_serial(),
                })
        return True

    def button_validate(self):
        for picking in self:
            if picking.state in ("done", "cancel") or picking.picking_type_code != "outgoing":
                continue
            expected = picking._scratch_expected_types()
            lines = picking.scratch_card_line_ids
            if Counter(lines.mapped("prefix")) != Counter(expected) or any(not line.packed for line in lines):
                raise UserError(_("Prepare and check every Scratch & Win card and serial number before validating this delivery."))
        return super().button_validate()
