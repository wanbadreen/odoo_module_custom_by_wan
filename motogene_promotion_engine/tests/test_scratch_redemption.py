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
            "name": "Reusable Redemption Promo", "reward_type": "scratch_cards", "minimum_amount": 100,
            "scratch_redemption_expiry_date": today,
            "date_start": today, "date_end": today,
        })
        self.pool = self.env["motogene.scratch.serial.pool"].create({
            "program_id": self.program.id, "prefix": "A", "first_number": 1, "last_number": 10,
            "prize_description": "Configured test rebate", "redemption_reward_type": "products",
            "reward_product_line_ids": [Command.create({"product_id": self.product.id, "quantity": 2})],
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
            "card_photo": base64.b64encode(b"test photo evidence"), "photo_filename": "test.png",
        }
        vals.update(overrides)
        return self.env["motogene.scratch.redemption"].create(vals)

    def test_confirm_snapshot_and_automatic_reward_line(self):
        record = self._redemption()
        lines = self.target_order.order_line
        record.action_confirm_redemption()
        self.assertEqual(record.state, "confirmed")
        self.assertEqual(self.card.state, "redeemed")
        self.assertEqual(self.card.redemption_id, record)
        self.assertEqual(record.redeemed_by_id, self.env.user)
        self.assertTrue(record.redeemed_at)
        self.pool.prize_description = "Future prize setting"
        self.assertTrue(record.prize_snapshot.startswith("Configured test rebate"))
        self.assertEqual(len(self.target_order.order_line), len(lines) + 1)
        self.assertEqual(record.reward_line_ids.product_uom_qty, 2)
        self.assertEqual(record.reward_line_ids.price_unit, 0)
        self.target_order.action_recompute_motogene_promotions()
        self.assertTrue(record.reward_line_ids.exists())

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
        self.env.cr.execute("UPDATE motogene_scratch_picking_card SET expiry_date = %s WHERE id = %s", [self.today - timedelta(days=1), self.card.id])
        self.card.invalidate_recordset()
        self.card.modified(["expiry_date"])
        record.invalidate_recordset()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()
        self.env.cr.execute("UPDATE motogene_scratch_picking_card SET expiry_date = %s WHERE id = %s", [self.today, self.card.id])
        self.card.invalidate_recordset()
        self.card.modified(["expiry_date"])
        record.invalidate_recordset()
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
        for vals in ({"state": "draft"}, {"card_photo": False}, {"mystery_product_id": self.product.id}):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                record.write(vals)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.unlink()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.card.state = "sent"
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.card.redemption_id = False


    def test_fixed_expiry_snapshot_and_legacy_backfill(self):
        self.assertEqual(self.card.expiry_date, self.today)
        self.program.scratch_redemption_expiry_date = self.today + timedelta(days=30)
        self.assertEqual(self.card.expiry_date, self.today)
        self.env.cr.execute("UPDATE motogene_scratch_picking_card SET expiry_date = NULL WHERE id = %s", [self.card.id])
        self.card.invalidate_recordset()
        self.program.scratch_redemption_expiry_date = self.today + timedelta(days=31)
        self.assertEqual(self.card.expiry_date, self.today + timedelta(days=31))
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.card.expiry_date = self.today

    def test_wizard_customer_filter_and_duplicate(self):
        self.assertIn(self.card, self.target_order._redeemable_scratch_cards())
        wizard = self.env["motogene.scratch.redemption.wizard"].create({
            "sale_id": self.target_order.id, "card_id": self.card.id,
            "card_photo": base64.b64encode(b"photo"),
        })
        wizard.action_confirm()
        self.assertEqual(self.card.state, "redeemed")
        self.assertNotIn(self.card, self.target_order._redeemable_scratch_cards())
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_confirm()
        other = self.env["res.partner"].create({"name": "Unrelated account"})
        unrelated = self.env["sale.order"].create({"partner_id": other.id})
        self.assertFalse(unrelated._redeemable_scratch_cards())

    def test_cancel_restores_card_and_preserves_history(self):
        record = self._redemption()
        record.action_confirm_redemption()
        rewards = record.reward_line_ids
        self.target_order.action_cancel()
        self.assertEqual(record.state, "cancelled")
        self.assertTrue(record.cancelled_at)
        self.assertEqual(self.card.state, "sent")
        self.assertFalse(self.card.redemption_id)
        self.assertFalse(rewards.exists())
        self.target_order.action_draft()
        self.assertIn(self.card, self.target_order._redeemable_scratch_cards())
        second = self._redemption()
        second.action_confirm_redemption()
        self.assertEqual(self.card.redemption_id, second)
        self.assertEqual(record.state, "cancelled")

    def test_reward_edit_and_paid_purchase_removal_blocked(self):
        paid = self.target_order.order_line
        record = self._redemption()
        record.action_confirm_redemption()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.reward_line_ids.unlink()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.reward_line_ids.price_unit = 1
        with self.assertRaises(UserError), self.env.cr.savepoint():
            paid.unlink()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            paid.price_unit = 0

    def test_rebate_and_purchase_cap(self):
        rebate_product = self.env["product.product"].create({"name": "Scratch Rebate", "type": "service"})
        self.pool.write({"redemption_reward_type": "rebate", "rebate_product_id": rebate_product.id, "rebate_amount": 10})
        record = self._redemption()
        record.action_confirm_redemption()
        self.assertEqual(record.reward_line_ids.price_unit, -10)
        self.assertEqual(record.reward_line_ids.product_id, rebate_product)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.target_order.order_line.filtered(lambda l: not l.is_motogene_promo_reward).price_unit = 5

    def test_missing_reward_configuration_rolls_back(self):
        self.pool.reward_product_line_ids.unlink()
        record = self._redemption()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()
        self.assertEqual(record.state, "draft")
        self.assertEqual(self.card.state, "sent")
        self.assertFalse(record.reward_line_ids)

    def test_mystery_product_required_and_added(self):
        self.pool.redemption_reward_type = "mystery"
        record = self._redemption()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()
        record.mystery_product_id = self.product
        record.action_confirm_redemption()
        self.assertEqual(record.reward_line_ids.product_id, self.product)
        self.assertEqual(record.reward_line_ids.price_unit, 0)

    def test_delivered_order_cannot_restore_card(self):
        record = self._redemption()
        record.action_confirm_redemption()
        self.target_order.action_confirm()
        picking = self.target_order.picking_ids.filtered(lambda p: p.picking_type_code == "outgoing")[:1]
        self.assertTrue(picking)
        picking.move_ids.write({"quantity": 1, "picked": True})
        picking._action_done()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.target_order.action_cancel()
        self.assertEqual(self.card.state, "redeemed")
        self.assertEqual(record.state, "confirmed")


    def test_copy_order_does_not_copy_redemption_reward(self):
        record = self._redemption()
        record.action_confirm_redemption()
        copied = self.target_order.copy()
        self.assertFalse(copied.scratch_redemption_ids)
        self.assertFalse(copied.order_line.scratch_redemption_id)
        self.assertEqual(len(copied.order_line), 1)
        self.assertEqual(self.card.state, "redeemed")

    def _setup_transfer_file(self, destination):
        import json
        self.program.write({
            "scratch_extra_prefix": "A",
            "scratch_package_line_ids": [Command.create({
                "product_tmpl_id": self.product.product_tmpl_id.id,
                "advertised_cards": 1, "card_prefixes": "A",
            })],
        })
        return self.env["motogene.scratch.setup.transfer"].create({
            "program_id": destination.id, "mode": "import",
            "file_data": base64.b64encode(json.dumps(self.program._scratch_setup_payload()).encode()),
        })

    def _transfer_destination(self):
        return self.env["motogene.promotion.program"].create({
            "name": "Destination Promotion", "reward_type": "scratch_cards",
            "minimum_amount": 100, "date_start": self.today, "date_end": self.today,
        })

    def test_setup_transfer_roundtrip_without_history_or_counter(self):
        destination = self._transfer_destination()
        wizard = self._setup_transfer_file(destination)
        payload = self.program._scratch_setup_payload()
        self.assertNotIn("next_number", payload["pools"][0])
        self.assertNotIn("cards", payload)
        wizard.action_import()
        self.assertEqual(destination.state, "draft")
        self.assertEqual(destination.scratch_redemption_expiry_date, self.today)
        self.assertEqual(destination.scratch_package_line_ids.product_tmpl_id, self.product.product_tmpl_id)
        self.assertEqual(destination.scratch_serial_pool_ids.reward_product_line_ids.product_id, self.product)
        self.assertEqual(destination.scratch_serial_pool_ids.reward_product_line_ids.quantity, 2)
        self.assertEqual(destination.scratch_serial_pool_ids.next_number, 1)
        # Reimport updates the same prefix, not duplicate serial pools or products.
        wizard.action_import()
        self.assertEqual(len(destination.scratch_serial_pool_ids), 1)
        self.assertEqual(len(destination.scratch_package_line_ids), 1)

    def test_setup_transfer_missing_product_leaves_destination_unchanged(self):
        import json
        destination = self._transfer_destination()
        wizard = self._setup_transfer_file(destination)
        payload = json.loads(base64.b64decode(wizard.file_data))
        payload["pools"][0]["products"][0]["product"] = {"name": "DOES-NOT-EXIST-IMPORT-TEST", "code": "NO-SUCH-SKU", "xmlid": None}
        wizard.file_data = base64.b64encode(json.dumps(payload).encode())
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_import()
        self.assertFalse(destination.scratch_serial_pool_ids)
        self.assertFalse(destination.scratch_package_line_ids)
        self.assertFalse(destination.scratch_redemption_expiry_date)

    def test_setup_transfer_used_promotion_cannot_be_overwritten(self):
        wizard = self._setup_transfer_file(self.program)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_import()
        self.assertEqual(self.card.state, "sent")
        self.assertEqual(self.card.expiry_date, self.today)


    def test_unused_pool_start_change_moves_counter(self):
        pool = self.env["motogene.scratch.serial.pool"].create({
            "program_id": self.program.id, "prefix": "B", "first_number": 8001, "last_number": 8150,
        })
        pool.first_number = 8100
        self.assertEqual(pool.next_number, 8100)
        self.assertEqual(pool.reserve_serial(), "B8100")
        self.assertEqual(pool.next_number, 8101)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            pool.first_number = 8102
        with self.assertRaises(UserError), self.env.cr.savepoint():
            pool.next_number = 8100
        pool.last_number = 8100
        self.assertEqual(pool.next_number, 8101)

    def test_pool_with_card_history_cannot_change_start(self):
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.pool.write({"first_number": 2})
        self.assertEqual(self.pool.first_number, 1)

    def test_live_pool_search_and_reward_dependency(self):
        record = self._redemption()
        Model = self.env["motogene.scratch.redemption"]
        self.assertEqual(record.pool_id, self.pool)
        self.assertEqual(Model.search([("id", "=", record.id), ("pool_id", "=", self.pool.id)]), record)
        self.assertFalse(Model.search([("id", "=", record.id), ("pool_id", "=", False)]))
        self.assertFalse(Model.search([("id", "=", record.id), ("pool_id", "not in", self.pool.ids)]))
        self.pool.write({"redemption_reward_type": "mystery"})
        self.assertEqual(record.reward_type, "mystery")
        # A pool created later must become the live match without a stored stale link.
        self.card.write({"prefix": "B"})
        self.assertFalse(record.pool_id)
        self.assertEqual(Model.search([("id", "=", record.id), ("pool_id", "=", False)]), record)
        pool = self.env["motogene.scratch.serial.pool"].create({
            "program_id": self.program.id, "prefix": "B", "first_number": 900001,
            "last_number": 900010, "redemption_reward_type": "mystery"})
        self.assertEqual(record.pool_id, pool)
        self.assertEqual(Model.search([("id", "=", record.id), ("pool_id", "in", pool.ids)]), record)
        self.assertEqual(record.reward_type, "mystery")
