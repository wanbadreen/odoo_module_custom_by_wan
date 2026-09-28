# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


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
        # Row lock serializes simultaneous preparations of different deliveries.
        self.env.cr.execute(
            "SELECT next_number, last_number FROM motogene_scratch_serial_pool WHERE id = %s FOR UPDATE",
            [self.id],
        )
        number, maximum = self.env.cr.fetchone()
        if number > maximum:
            raise UserError(_("Scratch card type %s has no serial numbers remaining.") % self.prefix)
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

    picking_id = fields.Many2one("stock.picking", required=True, ondelete="cascade", index=True)
    sale_id = fields.Many2one(related="picking_id.sale_id", store=True)
    program_id = fields.Many2one("motogene.promotion.program", required=True, ondelete="restrict")
    prefix = fields.Char(string="Card Type", required=True)
    serial_number = fields.Char(required=True, readonly=True, copy=False)
    _sql_constraints = [
        ("scratch_card_serial_unique", "UNIQUE(serial_number)", "This scratch card serial is already allocated."),
    ]
