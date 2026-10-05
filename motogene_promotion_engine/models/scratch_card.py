# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class ScratchSerialShortage(UserError):
    """Only exhausted serial pools may be explicitly deferred."""


class ScratchSerialPool(models.Model):
    _name = "motogene.scratch.serial.pool"
    _description = "Scratch & Win Serial Pool"
    _order = "prefix"

    program_id = fields.Many2one("motogene.promotion.program", required=True, ondelete="cascade")
    prefix = fields.Char(required=True, size=1, help="Card type, e.g. A")
    first_number = fields.Integer(required=True, default=8001)
    last_number = fields.Integer(required=True)
    next_number = fields.Integer(required=True, default=8001, readonly=True)
    prize_description = fields.Char(
        string="Redemption Prize", help="Gift or rebate printed on this card type. Used for CS verification; no SO reward is added automatically.",
    )
    _sql_constraints = [
        ("scratch_serial_pool_unique", "UNIQUE(program_id, prefix)", "Only one pool per card type is allowed."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "next_number" not in vals:
                vals["next_number"] = vals.get("first_number", 8001)
        return super().create(vals_list)

    @api.constrains("prefix", "first_number", "last_number", "next_number")
    def _check_pool(self):
        for pool in self:
            if (len(pool.prefix or "") != 1 or not pool.prefix.isalpha()
                    or pool.prefix != pool.prefix.upper()
                    or pool.first_number <= 0 or pool.last_number < pool.first_number
                    or not pool.first_number <= pool.next_number <= pool.last_number + 1):
                raise ValidationError(_("Enter an uppercase card type and a valid serial range."))

    def reserve_serial(self):
        self.ensure_one()
        self.env["motogene.scratch.picking.card"].flush_model([
            "state", "serial_number", "program_id", "prefix",
        ])
        # Row lock serializes simultaneous preparations of different deliveries.
        self.env.cr.execute(
            "SELECT next_number, last_number FROM motogene_scratch_serial_pool WHERE id = %s FOR UPDATE",
            [self.id],
        )
        number, maximum = self.env.cr.fetchone()
        self.env.cr.execute("""
            SELECT released.serial_number
              FROM motogene_scratch_picking_card released
             WHERE released.program_id = %s AND released.prefix = %s
               AND released.state = 'released'
               AND NOT EXISTS (
                   SELECT 1 FROM motogene_scratch_picking_card active
                    WHERE active.serial_number = released.serial_number
                      AND active.state != 'released'
               )
             ORDER BY released.id LIMIT 1
        """, [self.program_id.id, self.prefix])
        reusable = self.env.cr.fetchone()
        if reusable:
            return reusable[0]
        if number > maximum:
            raise ScratchSerialShortage(_("Scratch card type %s has no serial numbers remaining.") % self.prefix)
        self.env.cr.execute(
            "UPDATE motogene_scratch_serial_pool SET next_number = %s WHERE id = %s",
            [number + 1, self.id],
        )
        self.invalidate_recordset(["next_number"])
        return "%s%d" % (self.prefix, number)


class ScratchPickingCard(models.Model):
    _name = "motogene.scratch.picking.card"
    _description = "Scratch & Win Card to Pack"
    _order = "id"
    _rec_name = "serial_number"

    picking_id = fields.Many2one("stock.picking", ondelete="restrict", index=True, copy=False)
    sale_id = fields.Many2one("sale.order", required=True, ondelete="restrict", index=True, copy=False)
    program_id = fields.Many2one("motogene.promotion.program", required=True, ondelete="restrict")
    prefix = fields.Char(string="Card Type", required=True)
    serial_number = fields.Char(required=True, readonly=True, copy=False)
    state = fields.Selection([
        ("reserved", "Allocated"), ("sent", "Dispatched"), ("released", "Released"),
        ("void", "Void — Returned"),
        ("redeemed", "Redeemed"),
    ], default="reserved", required=True, readonly=True, copy=False, index=True)
    return_picking_id = fields.Many2one(
        "stock.picking", string="Physical Card Return", readonly=True,
        ondelete="restrict", copy=False, index=True,
    )
    returned_at = fields.Datetime(string="Card Returned At", readonly=True, copy=False)
    returned_by_id = fields.Many2one(
        "res.users", string="Card Received By", readonly=True, copy=False,
    )
    expiry_date = fields.Date(string="Card Redemption Expiry", readonly=True, copy=False, index=True)
    redemption_id = fields.Many2one(
        "motogene.scratch.redemption", string="Card Redemption", readonly=True,
        ondelete="restrict", copy=False, index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        if any(v.get("state") == "redeemed" or v.get("redemption_id") for v in vals_list):
            raise UserError(_("Use Confirm Redemption to redeem a scratch card."))
        for vals in vals_list:
            if not vals.get("expiry_date"):
                program = self.env["motogene.promotion.program"].browse(vals.get("program_id"))
                vals["expiry_date"] = program.scratch_redemption_expiry_date
        return super().create(vals_list)

    def write(self, vals):
        identity = {"state", "serial_number", "sale_id", "picking_id", "program_id", "prefix", "redemption_id", "expiry_date"}
        if set(vals) & identity and self.ids:
            self.flush_recordset(["state", "expiry_date"])
            self.env.cr.execute(
                "SELECT id FROM motogene_scratch_picking_card WHERE id IN %s ORDER BY id FOR UPDATE",
                [tuple(sorted(self.ids))],
            )
            self.invalidate_recordset(["state", "expiry_date"])
        if "expiry_date" in vals and any(c.expiry_date for c in self):
            raise UserError(_("An allocated card's expiry cannot be changed."))
        if "redemption_id" in vals or vals.get("state") == "redeemed":
            raise UserError(_("Use Confirm Redemption to redeem a scratch card."))
        if any(c.state == "redeemed" for c in self) and set(vals) & {
            "state", "serial_number", "sale_id", "picking_id", "program_id", "prefix",
        }:
            raise UserError(_("A redeemed card's identity and status cannot be changed."))
        return super().write(vals)

    def _mark_redeemed(self, redemption):
        self.ensure_one()
        if redemption.card_id != self or redemption.state != "confirmed":
            raise UserError(_("The confirmed redemption must match this card."))
        if self.state != "sent":
            raise UserError(_("Only a dispatched card can be redeemed."))
        return super(ScratchPickingCard, self).write({
            "state": "redeemed", "redemption_id": redemption.id,
        })

    def _restore_after_redemption_cancel(self, redemption):
        self.ensure_one()
        if self.state != "redeemed" or self.redemption_id != redemption or redemption.state != "cancelled":
            raise UserError(_("Only the matching cancelled redemption can restore this card."))
        return super(ScratchPickingCard, self).write({"state": "sent", "redemption_id": False})

    def init(self):
        self.env.cr.execute("""
            ALTER TABLE motogene_scratch_picking_card
            DROP CONSTRAINT IF EXISTS motogene_scratch_picking_card_scratch_card_serial_unique
        """)
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS motogene_scratch_active_serial_unique
            ON motogene_scratch_picking_card (serial_number)
            WHERE state != 'released'
        """)
