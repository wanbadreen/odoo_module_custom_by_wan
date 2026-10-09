# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression


def scratch_pool_search_domain(env, operator, value):
    """Search the live promo/prefix match, including cards with no pool."""
    if operator not in ("=", "!=", "in", "not in"):
        raise UserError(_("Unsupported scratch pool search operator: %s") % operator)
    values = list(value) if operator in ("in", "not in") else [value]
    Pool = env["motogene.scratch.serial.pool"]
    pools = Pool.browse([v for v in values if v]).exists()
    def matches(records):
        return expression.OR([
            [("card_id.program_id", "=", p.program_id.id),
             ("card_id.prefix", "=", p.prefix)]
            for p in records
        ])
    domains = [matches(pools)]
    if any(not v for v in values):
        domains.append(["!"] + matches(Pool.search([])))
    domain = expression.OR(domains)
    return ["!"] + domain if operator in ("!=", "not in") else domain


class ScratchRedemption(models.Model):
    _name = "motogene.scratch.redemption"
    _description = "Scratch Card Redemption"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "card_id"
    _order = "id desc"

    card_id = fields.Many2one(
        "motogene.scratch.picking.card", string="Card Serial", required=True,
        ondelete="restrict", index=True, copy=False, tracking=True,
    )
    card_state = fields.Selection(related="card_id.state", string="Current Card Status")
    program_id = fields.Many2one(related="card_id.program_id", string="Card Promotion")
    source_sale_id = fields.Many2one(related="card_id.sale_id", string="Original Sales Order")
    source_picking_id = fields.Many2one(related="card_id.picking_id", string="Original Delivery Order")
    partner_id = fields.Many2one(related="card_id.sale_id.partner_id", string="Card Recipient")
    commercial_partner_id = fields.Many2one(related="partner_id.commercial_partner_id", string="Card Recipient Account")
    company_id = fields.Many2one(related="card_id.sale_id.company_id", store=True, index=True)
    prize_preview = fields.Char(string="Configured Prize", compute="_compute_prize_preview")
    expiry_date = fields.Date(related="card_id.expiry_date", string="Card Redemption Expiry", store=True, readonly=True)
    card_photo = fields.Binary(string="Scratched Card Photo", attachment=True, copy=False)
    photo_filename = fields.Char(string="Card Photo Filename")
    target_sale_id = fields.Many2one(
        "sale.order", string="Redemption Sales Order", required=True,
        ondelete="restrict", copy=False, tracking=True,
    )
    notes = fields.Text(string="CS Verification Notes")
    prize_snapshot = fields.Char(string="Prize at Redemption", readonly=True, copy=False)
    state = fields.Selection([
        ("draft", "Draft"), ("confirmed", "Confirmed"), ("cancelled", "Cancelled"),
    ], default="draft", required=True, readonly=True, copy=False, index=True, tracking=True)
    redeemed_at = fields.Datetime(string="Redeemed At", readonly=True, copy=False)
    redeemed_by_id = fields.Many2one("res.users", string="Redeemed By", readonly=True, copy=False)

    @api.depends("card_id", "card_id.prefix", "card_id.program_id.scratch_serial_pool_ids.prize_description")
    def _compute_prize_preview(self):
        for record in self:
            pool = record.card_id.program_id.scratch_serial_pool_ids.filtered(
                lambda p: p.prefix == record.card_id.prefix
            )[:1]
            record.prize_preview = pool.prize_description if pool else False

    reward_line_ids = fields.One2many("sale.order.line", "scratch_redemption_id", string="Redemption Reward Lines", readonly=True)
    mystery_product_id = fields.Many2one("product.product", string="Mystery Gift Product", domain=[("sale_ok", "=", True)])
    reward_type = fields.Selection(related="pool_id.redemption_reward_type")
    pool_id = fields.Many2one("motogene.scratch.serial.pool", compute="_compute_pool", search="_search_pool")
    cancelled_at = fields.Datetime(readonly=True, copy=False)
    cancelled_by_id = fields.Many2one("res.users", readonly=True, copy=False)

    @api.depends("card_id", "card_id.prefix", "card_id.program_id.scratch_serial_pool_ids.prefix")
    def _compute_pool(self):
        for record in self:
            record.pool_id = record.card_id.program_id.scratch_serial_pool_ids.filtered(lambda p: p.prefix == record.card_id.prefix)[:1]

    @api.model
    def _search_pool(self, operator, value):
        return scratch_pool_search_domain(self.env, operator, value)

    def init(self):
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS motogene_scratch_confirmed_redemption_unique
            ON motogene_scratch_redemption (card_id) WHERE state = 'confirmed'
        """)

    @api.model_create_multi
    def create(self, vals_list):
        protected = {"prize_snapshot", "redeemed_at", "redeemed_by_id", "cancelled_at", "cancelled_by_id"}
        for vals in vals_list:
            if vals.get("state", "draft") != "draft" or set(vals) & protected:
                raise UserError(_("Create a draft and use Confirm Redemption."))
        return super().create(vals_list)

    def write(self, vals):
        self._lock_redemptions()
        if set(vals) & {"state", "prize_snapshot", "redeemed_at", "redeemed_by_id", "cancelled_at", "cancelled_by_id"}:
            raise UserError(_("Use Confirm Redemption to change redemption status."))
        if any(r.state != "draft" for r in self) and set(vals) & {
            "card_id", "expiry_date", "card_photo", "photo_filename", "target_sale_id", "notes", "mystery_product_id",
        }:
            raise UserError(_("A confirmed redemption cannot be edited."))
        return super().write(vals)

    def unlink(self):
        self._lock_redemptions()
        if any(r.state != "draft" for r in self):
            raise UserError(_("A confirmed redemption cannot be deleted."))
        return super().unlink()

    def _lock_redemptions(self):
        if self.ids:
            self.flush_recordset()
            self.env.cr.execute(
                "SELECT id FROM motogene_scratch_redemption WHERE id IN %s ORDER BY id FOR UPDATE",
                [tuple(sorted(self.ids))],
            )
            self.invalidate_recordset()

    @api.constrains("card_id", "target_sale_id")
    def _check_redemption_order(self):
        for record in self:
            order = record.target_sale_id
            if not record.card_id or not order:
                continue
            if order == record.source_sale_id:
                raise ValidationError(_("Select a separate Sales Order for the next purchase."))
            if order.date_order and record.source_sale_id.date_order and order.date_order < record.source_sale_id.date_order:
                raise ValidationError(_("The redemption quotation cannot be dated before the original purchase."))
            if order.company_id != record.company_id:
                raise ValidationError(_("The redemption order must belong to the card's company."))
            if order.partner_id.commercial_partner_id != record.partner_id.commercial_partner_id:
                raise ValidationError(_("The redemption order must belong to the card recipient."))

    def action_confirm_redemption(self):
        self.ensure_one()
        self.check_access_rights("write")
        self.check_access_rule("write")
        order = self.target_sale_id
        order.flush_recordset()
        self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [order.id])
        self._lock_redemptions()
        if self.state != "draft":
            raise UserError(_("This redemption is already confirmed."))
        card = self.card_id
        order = self.target_sale_id
        # Lock the target SO against simultaneous cancellation/customer edits,
        # then the card against a simultaneous redemption or physical return.
        order.flush_recordset()
        self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [order.id])
        order.invalidate_recordset()
        card.flush_recordset()
        self.env.cr.execute("SELECT id FROM motogene_scratch_picking_card WHERE id = %s FOR UPDATE", [card.id])
        card.invalidate_recordset()
        self.invalidate_recordset(["prize_preview", "partner_id", "company_id"])
        if card.state != "sent" or card.redemption_id:
            raise UserError(_("Card %s is not available for redemption. Only Dispatched cards can be redeemed.") % card.serial_number)
        if not card.picking_id or card.picking_id.state != "done":
            raise UserError(_("The original delivery must be completed before redemption."))
        self._check_redemption_order()
        if order.state not in ("draft", "sent"):
            raise UserError(_("Redeem on a draft quotation before Sales Order confirmation."))
        if not any(line.product_id and not line.display_type and not line.is_motogene_promo_reward
                   and line.product_uom_qty > 0 and line.price_subtotal > 0 for line in order.order_line):
            raise UserError(_("The redemption quotation must contain a paid purchase."))
        if not self.expiry_date or self.expiry_date < fields.Date.context_today(self):
            raise UserError(_("The card redemption expiry has passed or is missing. Set the fixed expiry in the promotion for legacy cards."))
        if not self.card_photo:
            raise UserError(_("Attach a photo showing the scratched prize and serial number."))
        if not self.prize_preview:
            raise UserError(_("Configure Redemption Prize for card type %s in the promotion's Serial Pools.") % card.prefix)
        snapshot = self._add_reward_to_order()
        super(ScratchRedemption, self).write({
            "state": "confirmed", "prize_snapshot": snapshot,
            "redeemed_at": fields.Datetime.now(), "redeemed_by_id": self.env.user.id,
        })
        card._mark_redeemed(self)
        self.message_post(body=_("Redemption confirmed and reward added to the quotation."))
        order.message_post(body=_("Scratch card %s redeemed; its reward was added automatically.") % card.serial_number)
        return True


    def _add_reward_to_order(self):
        self.ensure_one()
        pool, order = self.pool_id, self.target_sale_id
        if not pool or not pool.redemption_reward_type:
            raise UserError(_("Configure the reward type for this card prefix."))
        pool._validate_redemption_reward()
        rewards = []
        if pool.redemption_reward_type == "rebate":
            if order.currency_id != order.company_id.currency_id:
                raise UserError(_("Rebate redemption currently requires the company currency."))
            paid_total = sum(l.price_subtotal for l in order.order_line if not l.display_type and not l.is_motogene_promo_reward)
            existing_rebates = -sum(l.price_subtotal for l in order.order_line if l.scratch_redemption_id and l.price_subtotal < 0)
            if pool.rebate_amount + existing_rebates > paid_total:
                raise UserError(_("Total scratch card rebates cannot exceed the paid purchase amount."))
            rewards = [(pool.rebate_product_id, 1, -pool.rebate_amount)]
        elif pool.redemption_reward_type == "products":
            rewards = self._selected_free_product_rewards(pool)
        else:
            if not self.mystery_product_id or not self.mystery_product_id.sale_ok:
                raise UserError(_("Choose a saleable mystery gift product before confirming."))
            rewards = [(self.mystery_product_id, pool.mystery_quantity, 0)]
        names = []
        for product, quantity, price in rewards:
            if product.company_id and product.company_id != order.company_id:
                raise UserError(_("Reward products must belong to the quotation company or be shared."))
            self.env["sale.order.line"].with_context(motogene_skip_promotion_engine=True)._create_scratch_reward({
                "order_id": order.id, "product_id": product.id,
                "product_uom_qty": quantity, "product_uom": product.uom_id.id,
                "price_unit": price, "discount": 0,
                "name": _("[SCRATCH %s] %s") % (self.card_id.serial_number, product.display_name),
                "is_motogene_promo_reward": True, "promotion_program_id": self.program_id.id,
                "scratch_redemption_id": self.id,
            }, self)
            names.append("%s × %s%s" % (quantity, product.display_name, " (%s %s)" % (price, order.currency_id.name) if price else ""))
        return "%s — %s" % (self.prize_preview, "; ".join(names))

    def _cancel_for_cancelled_order(self):
        for record in self.sorted("id"):
            record._lock_redemptions()
            if record.state != "confirmed":
                continue
            if record.target_sale_id.state != "cancel":
                raise UserError(_("Cancel the quotation to reverse redemption."))
            card = record.card_id
            card.flush_recordset()
            self.env.cr.execute("SELECT id FROM motogene_scratch_picking_card WHERE id = %s FOR UPDATE", [card.id])
            card.invalidate_recordset()
            super(ScratchRedemption, record).write({
                "state": "cancelled", "cancelled_at": fields.Datetime.now(), "cancelled_by_id": self.env.user.id,
            })
            # Bypass only our line edit guard through a private method, never a client context flag.
            record.reward_line_ids._unlink_cancelled_scratch_rewards()
            card._restore_after_redemption_cancel(record)
            record.message_post(body=_("Redemption reversed because its Sales Order was cancelled. The card is Dispatched again; its original expiry still applies."))

