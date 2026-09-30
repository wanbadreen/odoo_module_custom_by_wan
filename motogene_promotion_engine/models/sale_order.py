# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
import math
from collections import Counter
from odoo.exceptions import UserError


SKIP_CTX = "motogene_skip_promotion_engine"


class SaleOrder(models.Model):
    _inherit = "sale.order"

    promotion_reward_line_ids = fields.One2many(
        "sale.order.line",
        "order_id",
        string="Promotion Reward Lines",
        domain=[("is_motogene_promo_reward", "=", True)],
        readonly=True,
    )

    lucky_draw_entries = fields.Integer(string="Lucky Draw Entries", copy=False, readonly=True)
    lucky_draw_program_id = fields.Many2one(
        "motogene.promotion.program", string="Lucky Draw Program",
        copy=False, readonly=True, ondelete="set null",
    )
    scratch_program_id = fields.Many2one(
        "motogene.promotion.program", string="Scratch & Win Program",
        copy=False, readonly=True, ondelete="set null",
    )
    scratch_base_cards = fields.Integer(string="Scratch Cards from Spend", copy=False, readonly=True)
    scratch_vip_cards = fields.Integer(string="Extra VIP Scratch Cards", copy=False, readonly=True)
    scratch_total_cards = fields.Integer(string="Total Scratch Cards", copy=False, readonly=True)
    scratch_card_line_ids = fields.One2many(
        "motogene.scratch.picking.card", "sale_id", string="Scratch Card Serial History",
        copy=False, readonly=True,
    )

    def _scratch_order_types(self):
        self.ensure_one()
        program = self.scratch_program_id
        if not program or not self.scratch_total_cards:
            return []
        packages = {p.product_tmpl_id.id: p for p in program.scratch_package_line_ids}
        types = []
        included = 0
        for line in self.order_line:
            package = packages.get(line.product_id.product_tmpl_id.id)
            if not package or not program._is_paid_scratch_package_line(line) or line.product_uom_qty <= 0:
                continue
            units = math.floor(float(line.product_uom_qty) + 1e-9)
            prefixes = [p.strip().upper() for p in (package.card_prefixes or "").split(",") if p.strip()]
            if len(prefixes) != package.advertised_cards:
                raise UserError(_("Configure the included card types for package %s before confirming.") % package.product_tmpl_id.display_name)
            types.extend(prefixes * units)
            included += len(prefixes) * units
        extra = self.scratch_base_cards - included
        if extra < 0:
            raise UserError(_("Package card allocations exceed the order's spend-based entitlement."))
        types.extend([program.scratch_extra_prefix] * extra)
        types.extend([program.scratch_vip_prefix] * self.scratch_vip_cards)
        return types

    def _allocate_scratch_cards(self):
        for order in self.filtered(lambda o: o.state == "sale").sorted("id"):
            # Serialize allocation/cancellation for the same SO.
            self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [order.id])
            order.invalidate_recordset(["scratch_card_line_ids"])
            order.scratch_card_line_ids.filtered(
                lambda c: c.state == "reserved" and c.picking_id.state == "cancel"
            ).write({"state": "released"})
            expected = Counter(order._scratch_order_types())
            cards = order.scratch_card_line_ids.filtered(lambda c: c.state != "released")
            # Keep dispatched cards; release any excess unshipped allocation.
            for prefix in set(cards.mapped("prefix")):
                matching = cards.filtered(lambda c: c.prefix == prefix)
                excess = max(0, len(matching) - expected[prefix])
                reserved = matching.filtered(lambda c: c.state == "reserved").sorted("id", reverse=True)
                reserved[:excess].write({"state": "released"})
            cards = order.scratch_card_line_ids.filtered(lambda c: c.state != "released")
            current = Counter(cards.mapped("prefix"))
            pools = {p.prefix: p for p in order.scratch_program_id.scratch_serial_pool_ids}
            for prefix in sorted(expected):
                missing = max(0, expected[prefix] - current[prefix])
                if missing and prefix not in pools:
                    raise UserError(_("Configure the serial range for card type %s before confirming.") % prefix)
                for _card in range(missing):
                    self.env["motogene.scratch.picking.card"].create({
                        "sale_id": order.id, "program_id": order.scratch_program_id.id,
                        "prefix": prefix, "serial_number": pools[prefix].reserve_serial(),
                    })
            order._assign_scratch_cards_to_delivery()
        return True

    def _assign_scratch_cards_to_delivery(self):
        for order in self:
            reserved = order.scratch_card_line_ids.filtered(
                lambda c: c.state == "reserved" and not c.picking_id
            )
            deliveries = order.picking_ids.filtered(
                lambda p: p.picking_type_code == "outgoing" and p.state not in ("done", "cancel")
            ).sorted("id")
            if reserved and deliveries:
                reserved.write({"picking_id": deliveries[0].id})

    def _release_reserved_scratch_cards(self):
        for order in self.sorted("id"):
            self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [order.id])
            order.invalidate_recordset(["scratch_card_line_ids"])
            cards = order.scratch_card_line_ids.filtered(lambda c: c.state == "reserved")
            pickings = cards.mapped("picking_id")
            cards.write({"state": "released"})
            pickings.invalidate_recordset(["scratch_card_line_ids"])

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        if not self.env.context.get(SKIP_CTX):
            orders.filtered(lambda o: o.state in ("draft", "sent"))._apply_motogene_promotions()
        return orders

    def write(self, vals):
        res = super().write(vals)
        if (
            not self.env.context.get(SKIP_CTX)
            and {"date_order", "company_id"}.intersection(vals)
        ):
            self.filtered(lambda o: o.state in ("draft", "sent"))._apply_motogene_promotions()
        return res

    def _promotion_programs_to_evaluate(self):
        self.ensure_one()
        Program = self.env["motogene.promotion.program"].sudo()
        active_programs = Program.search([
            ("company_id", "in", [False, self.company_id.id]),
            ("state", "=", "active"),
            ("active", "=", True),
        ])
        existing_programs = self.order_line.filtered(
            "is_motogene_promo_reward"
        ).mapped("promotion_program_id")
        return active_programs | existing_programs

    def _apply_motogene_promotions(self):
        """Idempotently add/update/remove generated reward lines."""
        for order in self:
            if order.state not in ("draft", "sent"):
                continue

            programs = order._promotion_programs_to_evaluate()
            for program in programs.sorted(key=lambda p: (p.priority, p.id)):
                if program.reward_type != "free_product":
                    continue
                expected_qty = program._reward_quantity_for_order(order)
                reward_lines = order.order_line.filtered(
                    lambda line: line.is_motogene_promo_reward
                    and line.promotion_program_id == program
                )

                if expected_qty <= 0:
                    if reward_lines:
                        reward_lines.with_context(**{SKIP_CTX: True}).unlink()
                    continue

                # Keep exactly one generated line per program.
                reward_line = reward_lines[:1]
                extras = reward_lines[1:]
                if extras:
                    extras.with_context(**{SKIP_CTX: True}).unlink()

                vals = {
                    "product_id": program.reward_product_id.id,
                    "product_uom_qty": expected_qty,
                    "price_unit": 0.0,
                    "name": _("[PROMO] %(label)s - %(program)s") % {
                        "label": program.reward_line_label or _("Promotion Reward"),
                        "program": program.name,
                    },
                    "is_motogene_promo_reward": True,
                    "promotion_program_id": program.id,
                }
                if reward_line:
                    reward_line.with_context(**{SKIP_CTX: True}).write(vals)
                else:
                    vals["order_id"] = order.id
                    self.env["sale.order.line"].with_context(**{SKIP_CTX: True}).create(vals)
        return True

    def _set_lucky_draw_entries(self):
        """Snapshot one eligible draw program when an order is confirmed."""
        Program = self.env["motogene.promotion.program"].sudo()
        for order in self:
            program = Program.search([
                ("company_id", "in", [False, order.company_id.id]),
                ("state", "=", "active"),
                ("active", "=", True),
                ("reward_type", "=", "lucky_draw_entries"),
            ], order="priority, id").filtered(lambda p: p._is_valid_for_order(order))[:1]
            entries = int(program._reward_quantity_for_order(order)) if program else 0
            order.with_context(**{SKIP_CTX: True}).write({
                "lucky_draw_program_id": program.id if program else False,
                "lucky_draw_entries": entries,
            })

    def _set_scratch_card_counts(self):
        """Snapshot card entitlement from the quotation date and eligible spend."""
        Program = self.env["motogene.promotion.program"].sudo()
        for order in self:
            program = Program.search([
                ("company_id", "in", [False, order.company_id.id]),
                ("state", "=", "active"), ("active", "=", True),
                ("reward_type", "=", "scratch_cards"),
            ], order="priority, id").filtered(lambda p: p._is_valid_for_order(order))[:1]
            base = int(program._reward_quantity_for_order(order)) if program else 0
            vip = (
                program._scratch_package_units_for_order(order)
                if program and program._is_scratch_vip_customer(order) else 0
            )
            order.with_context(**{SKIP_CTX: True}).write({
                "scratch_program_id": program.id if program else False,
                "scratch_base_cards": base,
                "scratch_vip_cards": vip,
                "scratch_total_cards": base + vip,
            })

    def action_recompute_motogene_promotions(self):
        self._apply_motogene_promotions()
        return True

    def action_confirm(self):
        # Final reconciliation before the delivery/invoice chain is generated.
        self.filtered(lambda o: o.state in ("draft", "sent"))._apply_motogene_promotions()
        # Odoo may replace date_order with the confirmation time. Snapshot the
        # quotation's order date and spend before that happens.
        self.filtered(lambda o: o.state in ("draft", "sent"))._set_lucky_draw_entries()
        self.filtered(lambda o: o.state in ("draft", "sent"))._set_scratch_card_counts()
        result = super().action_confirm()
        self._allocate_scratch_cards()
        return result

    def action_cancel(self):
        for order in self.sorted("id"):
            self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [order.id])
        result = super().action_cancel()
        self._release_reserved_scratch_cards()
        self.filtered(lambda o: o.state == "cancel").with_context(**{SKIP_CTX: True}).write({
            "lucky_draw_entries": 0,
            "scratch_base_cards": 0,
            "scratch_vip_cards": 0,
            "scratch_total_cards": 0,
        })
        return result


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    is_motogene_promo_reward = fields.Boolean(
        string="Promotion Reward",
        default=False,
        copy=False,
        index=True,
        readonly=True,
    )
    promotion_program_id = fields.Many2one(
        "motogene.promotion.program",
        string="Promotion Program",
        copy=False,
        index=True,
        readonly=True,
        ondelete="set null",
    )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get(SKIP_CTX):
            lines.mapped("order_id").filtered(
                lambda o: o.state in ("draft", "sent")
            )._apply_motogene_promotions()
        return lines

    def write(self, vals):
        orders = self.mapped("order_id")
        res = super().write(vals)
        if not self.env.context.get(SKIP_CTX):
            orders.filtered(lambda o: o.state in ("draft", "sent"))._apply_motogene_promotions()
        return res

    def unlink(self):
        orders = self.mapped("order_id")
        res = super().unlink()
        if not self.env.context.get(SKIP_CTX):
            orders.filtered(lambda o: o.exists() and o.state in ("draft", "sent"))._apply_motogene_promotions()
        return res
