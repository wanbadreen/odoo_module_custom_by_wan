import base64
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestScratchRedemption(TransactionCase):
    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].create({"name": "Redemption Test Customer"})
        self.product = self.env["product.product"].create({
            "name": "Redemption Test Purchase", "type": "consu", "list_price": 100,
        })
        self.source_order = self.env["sale.order"].create({"partner_id": self.partner.id})
        self.target_order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [Command.create({
                "product_id": self.product.id, "product_uom_qty": 1, "price_unit": 100,
            })],
        })
        today = fields.Date.context_today(self.env["motogene.scratch.redemption"])
        self.program = self.env["motogene.promotion.program"].create({
            "name": "Reusable Redemption Promo", "reward_product_id": self.product.id,
            "date_start": today, "date_end": today,
        })
        self.pool = self.env["motogene.scratch.serial.pool"].create({
            "program_id": self.program.id, "prefix": "A", "first_number": 1, "last_number": 10,
            "prize_description": "Configured test rebate",
        })
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1)
        self.delivery = self.env["stock.picking"].create({
            "partner_id": self.partner.id, "picking_type_id": warehouse.out_type_id.id,
            "location_id": warehouse.lot_stock_id.id,
            "location_dest_id": self.env.ref("stock.stock_location_customers").id,
        })
        self.env["stock.move"].create({
            "name": "Test delivery", "product_id": self.product.id,
            "product_uom_qty": 1, "product_uom": self.product.uom_id.id,
            "picking_id": self.delivery.id, "location_id": self.delivery.location_id.id,
            "location_dest_id": self.delivery.location_dest_id.id,
        })
        self.delivery.action_confirm()
        self.delivery.move_ids.write({"quantity": 1, "picked": True})
        self.delivery._action_done()
        self.card = self.env["motogene.scratch.picking.card"].create({
            "sale_id": self.source_order.id, "program_id": self.program.id,
            "picking_id": self.delivery.id, "prefix": "A", "serial_number": "REDEEM-TEST-A1", "state": "sent",
        })
        self.today = today

    def _redemption(self, **overrides):
        vals = {
            "card_id": self.card.id, "target_sale_id": self.target_order.id,
            "expiry_date": self.today,
            "card_photo": base64.b64encode(b"test photo evidence"), "photo_filename": "test.png",
        }
        vals.update(overrides)
        return self.env["motogene.scratch.redemption"].create(vals)

    def test_confirm_snapshot_and_no_reward_line(self):
        record = self._redemption()
        lines = self.target_order.order_line
        record.action_confirm_redemption()
        self.assertEqual(record.state, "confirmed")
        self.assertEqual(self.card.state, "redeemed")
        self.assertEqual(self.card.redemption_id, record)
        self.assertEqual(record.redeemed_by_id, self.env.user)
        self.assertTrue(record.redeemed_at)
        self.pool.prize_description = "Future prize setting"
        self.assertEqual(record.prize_snapshot, "Configured test rebate")
        self.assertEqual(self.target_order.order_line, lines)

    def test_duplicate_drafts_and_return_rejected_after_redeem(self):
        first, second = self._redemption(), self._redemption()
        first.action_confirm_redemption()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            second.action_confirm_redemption()
        self.assertEqual(second.state, "draft")
        returned = self.delivery.copy({
            "return_id": self.delivery.id, "move_ids": [], "state": "draft",
            "location_id": self.delivery.location_dest_id.id,
            "location_dest_id": self.delivery.location_id.id,
        })
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            returned.scratch_return_card_ids = [Command.set(self.card.ids)]

    def test_invalid_card_states_and_expiry(self):
        record = self._redemption()
        for state in ("reserved", "released", "void"):
            self.card.state = state
            with self.assertRaises(UserError), self.env.cr.savepoint():
                record.action_confirm_redemption()
        self.card.state = "sent"
        record.expiry_date = self.today - timedelta(days=1)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()
        record.expiry_date = self.today
        record.card_photo = False
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()

    def test_wrong_customer_original_order_and_empty_purchase(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._redemption(target_sale_id=self.source_order.id)
        other_partner = self.env["res.partner"].create({"name": "Different customer"})
        other = self.env["sale.order"].create({"partner_id": other_partner.id})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._redemption(target_sale_id=other.id)
        empty = self.env["sale.order"].create({"partner_id": self.partner.id})
        record = self._redemption(target_sale_id=empty.id)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()

    def test_confirmed_record_and_card_cannot_be_reset(self):
        record = self._redemption()
        record.action_confirm_redemption()
        for vals in ({"state": "draft"}, {"expiry_date": self.today + timedelta(days=30)}, {"card_photo": False}):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                record.write(vals)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.unlink()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.card.state = "sent"
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.card.redemption_id = False
