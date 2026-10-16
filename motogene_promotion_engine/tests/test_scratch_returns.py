from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestScratchCardReturns(TransactionCase):
    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].create({"name": "Scratch Return Customer"})
        self.product = self.env["product.product"].create({"name": "Return Test Box", "type": "consu"})
        self.order = self.env["sale.order"].create({"partner_id": self.partner.id})
        self.program = self.env["motogene.promotion.program"].create({
            "name": "Return Test Promotion", "reward_product_id": self.product.id,
            "date_start": fields.Date.today(), "date_end": fields.Date.today(),
        })
        self.warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1)
        self.customer_location = self.env.ref("stock.stock_location_customers")
        self.delivery = self._picking()
        self.delivery._action_done()
        self.cards = self.env["motogene.scratch.picking.card"].create([
            {"sale_id": self.order.id, "program_id": self.program.id,
             "picking_id": self.delivery.id, "prefix": "A", "serial_number": "RETURN-TEST-%s" % n,
             "state": "sent"} for n in (1, 2)
        ])

    def _picking(self, return_of=None, backorder_of=None, operation=None):
        incoming = bool(return_of or backorder_of)
        vals = {
            "partner_id": self.partner.id,
            "picking_type_id": (operation or (self.warehouse.in_type_id if incoming else self.warehouse.out_type_id)).id,
            "location_id": (self.customer_location if incoming else self.warehouse.lot_stock_id).id,
            "location_dest_id": (self.warehouse.lot_stock_id if incoming else self.customer_location).id,
            "return_id": return_of.id if return_of else False,
            "backorder_id": backorder_of.id if backorder_of else False,
        }
        picking = self.env["stock.picking"].create(vals)
        self.env["stock.move"].create({
            "name": self.product.name, "product_id": self.product.id,
            "product_uom_qty": 1, "product_uom": self.product.uom_id.id,
            "picking_id": picking.id, "location_id": picking.location_id.id,
            "location_dest_id": picking.location_dest_id.id,
        })
        picking.action_confirm()
        picking.move_ids.write({"quantity": 1, "picked": True})
        return picking

    def test_partial_physical_return_and_audit(self):
        returned = self._picking(return_of=self.delivery)
        returned.scratch_return_card_ids = [Command.set(self.cards[:1].ids)]
        self.assertEqual(self.cards[0].state, "sent")
        returned._action_done()
        self.assertEqual(self.cards[0].state, "void")
        self.assertEqual(self.cards[0].picking_id, self.delivery)
        self.assertEqual(self.cards[0].return_picking_id, returned)
        self.assertEqual(self.cards[0].returned_by_id, self.env.user)
        self.assertTrue(self.cards[0].returned_at)
        self.assertEqual(self.cards[1].state, "sent")
        with self.assertRaises(UserError):
            returned.scratch_return_card_ids = [Command.clear()]

    def test_product_only_and_cancel_do_not_void(self):
        returned = self._picking(return_of=self.delivery)
        returned._action_done()
        self.assertEqual(set(self.cards.mapped("state")), {"sent"})
        cancelled = self._picking(return_of=self.delivery)
        cancelled.scratch_return_card_ids = [Command.set(self.cards.ids)]
        cancelled.action_cancel()
        self.assertEqual(set(self.cards.mapped("state")), {"sent"})

    def test_wrong_delivery_and_duplicate_return_rejected(self):
        other_delivery = self._picking()
        other_delivery._action_done()
        wrong = self._picking(return_of=other_delivery)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            wrong.scratch_return_card_ids = [Command.set(self.cards.ids)]
        first = self._picking(return_of=self.delivery)
        second = self._picking(return_of=self.delivery)
        first.scratch_return_card_ids = [Command.set(self.cards[:1].ids)]
        second.scratch_return_card_ids = [Command.set(self.cards[:1].ids)]
        first._action_done()
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            second._action_done()
        self.assertNotEqual(second.state, "done")

    def test_backorder_and_outgoing_return_type(self):
        returned = self._picking(return_of=self.delivery, operation=self.warehouse.out_type_id)
        self.assertEqual(returned.scratch_return_source_id, self.delivery)
        returned.scratch_return_card_ids = [Command.set(self.cards[:1].ids)]
        backorder = self._picking(backorder_of=returned)
        self.assertEqual(backorder.scratch_return_source_id, self.delivery)
        self.assertFalse(backorder.scratch_return_card_ids)
        backorder.scratch_return_card_ids = [Command.set(self.cards[1:].ids)]
        backorder._action_done()
        self.assertEqual(self.cards[0].state, "sent")
        self.assertEqual(self.cards[1].state, "void")

    def test_void_serial_never_reused(self):
        pool = self.env["motogene.scratch.serial.pool"].create({
            "program_id": self.program.id, "prefix": "A", "first_number": 1, "last_number": 1,
        })
        self.cards[0].serial_number = "A1"
        pool.reserve_serial()
        returned = self._picking(return_of=self.delivery)
        returned.scratch_return_card_ids = [Command.set(self.cards[:1].ids)]
        returned._action_done()
        from ..models.scratch_card import ScratchSerialShortage
        with self.assertRaises(ScratchSerialShortage):
            pool.reserve_serial()
