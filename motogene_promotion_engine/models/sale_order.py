# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


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
            ("company_id", "=", self.company_id.id),
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

    def action_recompute_motogene_promotions(self):
        self._apply_motogene_promotions()
        return True

    def action_confirm(self):
        # Final reconciliation before the delivery/invoice chain is generated.
        self.filtered(lambda o: o.state in ("draft", "sent"))._apply_motogene_promotions()
        return super().action_confirm()


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
