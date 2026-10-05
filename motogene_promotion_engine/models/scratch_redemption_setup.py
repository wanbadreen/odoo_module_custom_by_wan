# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class ScratchPromotionSetup(models.Model):
    _inherit = "motogene.promotion.program"

    scratch_redemption_expiry_date = fields.Date(
        string="Card Redemption Expiry Date",
        help="Fixed last redemption date, inclusive. Copied to each allocated card. Existing card dates are never overwritten.",
    )

    def write(self, vals):
        result = super().write(vals)
        if vals.get("scratch_redemption_expiry_date"):
            for program in self:
                cards = self.env["motogene.scratch.picking.card"].search([
                    ("program_id", "=", program.id), ("expiry_date", "=", False),
                ])
                # One-time backfill for cards created before fixed-expiry support.
                cards.write({"expiry_date": program.scratch_redemption_expiry_date})
        return result

    @api.constrains("scratch_redemption_expiry_date", "date_end")
    def _check_scratch_expiry(self):
        for program in self:
            if program.scratch_redemption_expiry_date and program.date_end and program.scratch_redemption_expiry_date < program.date_end:
                raise ValidationError(_("Card redemption expiry cannot precede the promotion's final sales date."))


class ScratchRewardPool(models.Model):
    _inherit = "motogene.scratch.serial.pool"

    redemption_reward_type = fields.Selection([
        ("products", "Free Products"), ("rebate", "Rebate"), ("mystery", "Mystery Gift"),
    ], string="Redemption Reward Type")
    reward_product_line_ids = fields.One2many("motogene.scratch.reward.product", "pool_id", string="Free Products")
    rebate_amount = fields.Float(string="Rebate Amount")
    rebate_product_id = fields.Many2one("product.product", string="Rebate Service Product", domain=[("type", "=", "service"), ("sale_ok", "=", True)])
    mystery_quantity = fields.Float(string="Mystery Gift Quantity", default=1)

    def _validate_redemption_reward(self):
        self.ensure_one()
        if self.redemption_reward_type == "products" and not self.reward_product_line_ids:
            raise UserError(_("Configure at least one free product for prefix %s.") % self.prefix)
        if self.redemption_reward_type == "rebate" and (
            self.rebate_amount <= 0 or not self.rebate_product_id or self.rebate_product_id.type != "service" or not self.rebate_product_id.sale_ok
        ):
            raise UserError(_("Configure a positive rebate and a saleable service product for prefix %s.") % self.prefix)
        if self.redemption_reward_type == "mystery" and self.mystery_quantity <= 0:
            raise UserError(_("Mystery gift quantity must be positive."))
        if any(not r.product_id.sale_ok or r.quantity <= 0 for r in self.reward_product_line_ids):
            raise UserError(_("Free gifts require saleable products and positive quantities."))


class ScratchRewardProduct(models.Model):
    _name = "motogene.scratch.reward.product"
    _description = "Scratch Card Reward Product"

    pool_id = fields.Many2one("motogene.scratch.serial.pool", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True, domain=[("sale_ok", "=", True)], ondelete="restrict")
    quantity = fields.Float(required=True, default=1)

    @api.constrains("quantity")
    def _check_quantity(self):
        if any(r.quantity <= 0 for r in self):
            raise ValidationError(_("Reward quantity must be greater than zero."))


class ScratchRedemptionSaleOrder(models.Model):
    _inherit = "sale.order"

    scratch_redeem_available = fields.Boolean(compute="_compute_scratch_redeem_available", string="Has Redeemable Scratch Cards")

    def _redeemable_scratch_cards(self):
        self.ensure_one()
        if not self.partner_id or not self.id or self.state not in ("draft", "sent"):
            return self.env["motogene.scratch.picking.card"]
        return self.env["motogene.scratch.picking.card"].search([
            ("sale_id.partner_id.commercial_partner_id", "=", self.partner_id.commercial_partner_id.id),
            ("sale_id.company_id", "=", self.company_id.id), ("sale_id", "!=", self.id),
            ("state", "=", "sent"), ("redemption_id", "=", False),
            ("picking_id.state", "=", "done"), ("expiry_date", ">=", fields.Date.context_today(self)),
        ])

    @api.depends("partner_id", "company_id", "state")
    def _compute_scratch_redeem_available(self):
        for order in self:
            order.scratch_redeem_available = bool(order._redeemable_scratch_cards())

    def action_redeem_scratch_card(self):
        self.ensure_one()
        self.check_access_rights("write")
        self.check_access_rule("write")
        if not self._redeemable_scratch_cards():
            raise UserError(_("This customer has no dispatched, unredeemed cards within their redemption expiry. Refresh the quotation."))
        return {
            "type": "ir.actions.act_window", "name": _("Redeem Scratch Card"),
            "res_model": "motogene.scratch.redemption.wizard", "view_mode": "form", "target": "new",
            "view_id": self.env.ref("motogene_promotion_engine.view_scratch_redemption_wizard").id,
            "context": {"default_sale_id": self.id},
        }

    def _get_copiable_order_lines(self):
        return super()._get_copiable_order_lines().filtered(lambda line: not line.scratch_redemption_id)

    def _check_scratch_redemption_purchase(self):
        for order in self:
            records = order.scratch_redemption_ids.filtered(lambda r: r.state == "confirmed")
            if not records:
                continue
            for record in records:
                record._check_redemption_order()
            paid = sum(l.price_subtotal for l in order.order_line if l.product_uom_qty > 0 and not l.display_type and not l.is_motogene_promo_reward)
            rebates = -sum(l.price_subtotal for l in order.order_line if l.scratch_redemption_id and l.price_subtotal < 0)
            if paid <= 0 or rebates > paid:
                raise UserError(_("A redeemed quotation must retain a paid purchase covering its rebates. Cancel the quotation to reverse redemption."))

    def write(self, vals):
        # Serialize customer/amount/state changes with redemption confirmation.
        for order in self.sorted("id"):
            self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [order.id])
        self.invalidate_recordset(["scratch_redemption_ids"])
        records = self.scratch_redemption_ids.filtered(lambda r: r.state == "confirmed")
        if records and set(vals) & {"partner_id", "company_id", "pricelist_id", "currency_id"}:
            raise UserError(_("Cancel the quotation and reverse redemption before changing its customer, company or currency."))
        if vals.get("state") == "cancel" and records:
            orders = records.target_sale_id
            if orders.picking_ids.filtered(lambda p: p.state == "done") or any(l.qty_delivered or l.invoice_lines.filtered(lambda i: i.move_id.state != "cancel") for l in orders.order_line):
                raise UserError(_("Redemption cannot be automatically reversed after delivery or invoicing. Handle the physical return first."))
        result = super().write(vals)
        if vals.get("state") == "cancel":
            records._cancel_for_cancelled_order()
        if set(vals) & {"order_line", "date_order", "state"}:
            self.filtered(lambda o: o.state != "cancel")._check_scratch_redemption_purchase()
        return result

    def unlink(self):
        if self.scratch_redemption_ids:
            raise UserError(_("Keep this Sales Order for its scratch redemption history; cancel it instead."))
        return super().unlink()


class ScratchRedemptionSaleLine(models.Model):
    _inherit = "sale.order.line"

    scratch_redemption_id = fields.Many2one("motogene.scratch.redemption", readonly=True, copy=False, ondelete="restrict", index=True)

    @api.model_create_multi
    def create(self, vals_list):
        if any(v.get("scratch_redemption_id") for v in vals_list):
            raise UserError(_("Use Redeem Scratch Card to add redemption rewards."))
        lines = super().create(vals_list)
        lines.order_id.filtered(lambda o: o.state != "cancel")._check_scratch_redemption_purchase()
        return lines

    @api.model
    def _create_scratch_reward(self, vals, redemption):
        if redemption.state != "draft" or vals.get("order_id") != redemption.target_sale_id.id or vals.get("scratch_redemption_id") != redemption.id:
            raise UserError(_("Reward must match the draft redemption."))
        return super(ScratchRedemptionSaleLine, self).create([vals])

    def write(self, vals):
        if "scratch_redemption_id" in vals or (self.scratch_redemption_id and set(vals) & {
            "order_id", "product_id", "product_uom", "product_uom_qty", "price_unit", "discount", "tax_id",
            "is_motogene_promo_reward", "promotion_program_id", "display_type",
        }):
            raise UserError(_("Scratch redemption rewards cannot be edited. Cancel the quotation to reverse redemption."))
        orders = self.order_id
        for order in orders.sorted("id"):
            self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [order.id])
        result = super().write(vals)
        orders.filtered(lambda o: o.state != "cancel")._check_scratch_redemption_purchase()
        return result

    def unlink(self):
        if self.scratch_redemption_id:
            raise UserError(_("Scratch redemption rewards cannot be deleted. Cancel the quotation to reverse redemption."))
        orders = self.order_id
        for order in orders.sorted("id"):
            self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [order.id])
        result = super().unlink()
        orders.filtered(lambda o: o.state != "cancel")._check_scratch_redemption_purchase()
        return result

    def _unlink_cancelled_scratch_rewards(self):
        if any(not l.scratch_redemption_id or l.scratch_redemption_id.state != "cancelled" or l.order_id.state != "cancel" for l in self):
            raise UserError(_("Only cancelled redemption rewards can be removed."))
        return super(ScratchRedemptionSaleLine, self.with_context(motogene_skip_promotion_engine=True)).unlink()
