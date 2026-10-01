# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

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

    scratch_return_source_id = fields.Many2one(
        "stock.picking", string="Original Scratch Card Delivery",
        compute="_compute_scratch_return_source", store=True, index=True, recursive=True,
    )
    scratch_return_available_card_ids = fields.Many2many(
        "motogene.scratch.picking.card", string="Original Delivery Card History",
        compute="_compute_scratch_return_available_cards",
    )
    scratch_return_card_ids = fields.Many2many(
        "motogene.scratch.picking.card", "motogene_scratch_return_card_rel",
        "return_picking_id", "card_id", string="Physical Cards Received",
        copy=False,
        help="Select only physical cards received back. Product returns alone do not void cards.",
    )

    @api.depends("return_id", "return_id.state", "return_id.location_dest_id.usage",
                 "backorder_id", "backorder_id.scratch_return_source_id", "location_id.usage", "location_dest_id.usage")
    def _compute_scratch_return_source(self):
        for picking in self:
            source = self.env["stock.picking"]
            ancestor = picking
            while ancestor:
                if ancestor.return_id:
                    source = ancestor.return_id
                    break
                ancestor = ancestor.backorder_id
            # A return can reuse an outgoing operation type in standard Odoo.
            # Identify customer returns by locations rather than operation code.
            picking.scratch_return_source_id = source if (
                source and source.state == "done"
                and source.location_dest_id.usage == "customer"
                and picking.location_id.usage == "customer"
                and picking.location_dest_id.usage != "customer"
            ) else False

    @api.depends("scratch_return_source_id", "scratch_return_source_id.scratch_card_line_ids.state")
    def _compute_scratch_return_available_cards(self):
        for picking in self:
            picking.scratch_return_available_card_ids = picking.scratch_return_source_id.scratch_card_line_ids

    def write(self, vals):
        if "scratch_return_card_ids" in vals and any(p.state in ("done", "cancel") for p in self):
            raise UserError(_("Physical card selections cannot be changed on a completed or cancelled return."))
        return super().write(vals)

    @api.constrains("scratch_return_card_ids", "return_id", "location_id", "location_dest_id")
    def _check_scratch_return_selection(self):
        self._validate_scratch_return_cards()

    def _validate_scratch_return_cards(self):
        selected = set()
        for picking in self:
            for card in picking.scratch_return_card_ids:
                if card.id in selected:
                    raise ValidationError(_("Card %s cannot be received on two returns at once.") % card.serial_number)
                selected.add(card.id)
                if not picking.scratch_return_source_id or card.picking_id != picking.scratch_return_source_id:
                    raise ValidationError(_("Card %s does not belong to the original delivery.") % card.serial_number)
                completed_here = card.state == "void" and card.return_picking_id == picking and picking.state == "done"
                if card.state != "sent" and not completed_here:
                    raise ValidationError(_("Card %s is not Dispatched. It may already be returned or redeemed.") % card.serial_number)

    def _lock_scratch_return_cards(self):
        cards = self.mapped("scratch_return_card_ids").sorted("id")
        if cards:
            cards.flush_recordset(["state", "return_picking_id"])
            self.env.cr.execute(
                "SELECT id FROM motogene_scratch_picking_card WHERE id IN %s ORDER BY id FOR UPDATE",
                [tuple(cards.ids)],
            )
            cards.invalidate_recordset(["state", "return_picking_id"])
        self._validate_scratch_return_cards()

    def _void_received_scratch_cards(self):
        self._lock_scratch_return_cards()
        for picking in self.filtered(lambda p: p.state == "done"):
            cards = picking.scratch_return_card_ids.filtered(lambda c: c.state == "sent")
            if cards:
                cards.write({
                    "state": "void", "return_picking_id": picking.id,
                    "returned_at": fields.Datetime.now(), "returned_by_id": self.env.user.id,
                })
                picking.message_post(body=_("Physical scratch cards returned and voided: %s") % ", ".join(cards.mapped("serial_number")))

    def _scratch_expected_types(self):
        self.ensure_one()
        if self.picking_type_code != "outgoing":
            return []
        return self.sale_id._scratch_order_types() if self.sale_id else []

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get("scratch_return_card_ids") and vals.get("state") in ("done", "cancel") for vals in vals_list):
            raise UserError(_("Physical card selections must be recorded before validating the return."))
        pickings = super().create(vals_list)
        pickings.filtered(lambda p: p.picking_type_code == "outgoing" and not p.scratch_return_source_id).mapped("sale_id")._assign_scratch_cards_to_delivery()
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
        self._validate_scratch_return_cards()
        outgoing = self.filtered(lambda p: p.picking_type_code == "outgoing" and not p.scratch_return_source_id and p.state not in ("done", "cancel"))
        outgoing.mapped("sale_id").filtered(lambda order: not order.scratch_allocation_deferred)._allocate_scratch_cards()
        return super().button_validate()

    def _action_done(self):
        for order in self.mapped("sale_id").sorted("id"):
            self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [order.id])
        self._lock_scratch_return_cards()
        result = super()._action_done()
        for picking in self.filtered(lambda p: p.picking_type_code == "outgoing" and not p.scratch_return_source_id and p.state == "done"):
            picking.scratch_card_line_ids.filtered(lambda c: c.state == "reserved").write({"state": "sent"})
        self._void_received_scratch_cards()
        return result
