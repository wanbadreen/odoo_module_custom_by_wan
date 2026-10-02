# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


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
    expiry_date = fields.Date(string="Expiry Written on Card", required=True, tracking=True)
    card_photo = fields.Binary(string="Scratched Card Photo", attachment=True, copy=False)
    photo_filename = fields.Char(string="Card Photo Filename")
    target_sale_id = fields.Many2one(
        "sale.order", string="Redemption Sales Order", required=True,
        ondelete="restrict", copy=False, tracking=True,
    )
    notes = fields.Text(string="CS Verification Notes")
    prize_snapshot = fields.Char(string="Prize at Redemption", readonly=True, copy=False)
    state = fields.Selection([
        ("draft", "Draft"), ("confirmed", "Confirmed"),
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

    def init(self):
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS motogene_scratch_confirmed_redemption_unique
            ON motogene_scratch_redemption (card_id) WHERE state = 'confirmed'
        """)

    @api.model_create_multi
    def create(self, vals_list):
        protected = {"prize_snapshot", "redeemed_at", "redeemed_by_id"}
        for vals in vals_list:
            if vals.get("state", "draft") != "draft" or set(vals) & protected:
                raise UserError(_("Create a draft and use Confirm Redemption."))
        return super().create(vals_list)

    def write(self, vals):
        self._lock_redemptions()
        if set(vals) & {"state", "prize_snapshot", "redeemed_at", "redeemed_by_id"}:
            raise UserError(_("Use Confirm Redemption to change redemption status."))
        if any(r.state == "confirmed" for r in self) and set(vals) & {
            "card_id", "expiry_date", "card_photo", "photo_filename", "target_sale_id", "notes",
        }:
            raise UserError(_("A confirmed redemption cannot be edited."))
        return super().write(vals)

    def unlink(self):
        self._lock_redemptions()
        if any(r.state == "confirmed" for r in self):
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
            raise UserError(_("Use a draft quotation so CS can add the gift or rebate before confirmation."))
        if not any(line.product_id and not line.display_type and not line.is_motogene_promo_reward
                   and line.product_uom_qty > 0 and line.price_subtotal > 0 for line in order.order_line):
            raise UserError(_("The redemption quotation must contain a paid purchase."))
        if not self.expiry_date or self.expiry_date < fields.Date.context_today(self):
            raise UserError(_("The expiry date written on the card has passed or is missing."))
        if not self.card_photo:
            raise UserError(_("Attach a photo showing the scratched prize, serial number and expiry date."))
        if not self.prize_preview:
            raise UserError(_("Configure Redemption Prize for card type %s in the promotion's Serial Pools.") % card.prefix)
        super(ScratchRedemption, self).write({
            "state": "confirmed", "prize_snapshot": self.prize_preview,
            "redeemed_at": fields.Datetime.now(), "redeemed_by_id": self.env.user.id,
        })
        card._mark_redeemed(self)
        self.message_post(body=_("Redemption recorded. CS must manually add the configured prize or rebate to the redemption quotation."))
        order.message_post(body=_("Scratch card %s redeemed. CS must manually add the configured prize or rebate; this confirmation does not add any reward line.") % card.serial_number)
        return True
