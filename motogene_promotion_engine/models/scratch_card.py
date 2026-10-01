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
    ], default="reserved", required=True, readonly=True, copy=False, index=True)
    return_picking_id = fields.Many2one(
        "stock.picking", string="Physical Card Return", readonly=True,
        ondelete="restrict", copy=False, index=True,
    )
    returned_at = fields.Datetime(string="Card Returned At", readonly=True, copy=False)
    returned_by_id = fields.Many2one(
        "res.users", string="Card Received By", readonly=True, copy=False,
    )

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
