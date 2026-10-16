import base64
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestScratchGiftVariants(TransactionCase):
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

    def _variants(self):
        attribute = self.env['product.attribute'].create({'name': 'Gift Colour Test', 'create_variant': 'always'})
        values = self.env['product.attribute.value'].create([
            {'name': 'Blue', 'attribute_id': attribute.id},
            {'name': 'Orange', 'attribute_id': attribute.id},
        ])
        template = self.env['product.template'].create({
            'name': 'Test Gift Bag', 'type': 'consu',
            'attribute_line_ids': [Command.create({'attribute_id': attribute.id, 'value_ids': [Command.set(values.ids)]})],
        })
        variants = template.product_variant_ids.sorted('id')
        self.assertEqual(len(variants), 2)
        self.pool.reward_product_line_ids.unlink()
        line = self.env['motogene.scratch.reward.product'].create({
            'pool_id': self.pool.id, 'variant_mode': 'choose',
            'product_tmpl_id': template.id, 'quantity': 1,
        })
        return line, variants

    def test_selected_variant_and_fixed_gift_added_once(self):
        line, variants = self._variants()
        self.env['motogene.scratch.reward.product'].create({
            'pool_id': self.pool.id, 'product_id': self.product.id, 'quantity': 2})
        record = self._redemption(gift_choice_ids=[Command.create({
            'reward_line_id': line.id, 'product_id': variants[1].id})])
        record.action_confirm_redemption()
        rewards = record.reward_line_ids
        self.assertEqual(len(rewards), 2)
        self.assertEqual(rewards.filtered(lambda l: l.product_id == variants[1]).product_uom_qty, 1)
        self.assertEqual(rewards.filtered(lambda l: l.product_id == self.product).product_uom_qty, 2)
        self.assertNotIn(variants[0], rewards.mapped('product_id'))
        self.assertTrue(all(l.price_unit == 0 for l in rewards))
        self.assertIn(variants[1].display_name, record.prize_snapshot)
        self.target_order._apply_motogene_promotions()
        self.assertEqual(record.reward_line_ids, rewards)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.gift_choice_ids.write({'product_id': variants[0].id})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            rewards[:1].write({'product_id': variants[0].id})

    def test_missing_wrong_and_archived_variant_rejected(self):
        line, variants = self._variants()
        record = self._redemption()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()
        record.action_load_gift_choices()
        record.gift_choice_ids.product_id = self.product
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()
        record.gift_choice_ids.product_id = variants[0]
        variants[0].active = False
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()
        self.assertEqual(self.card.state, 'sent')
        self.assertFalse(record.reward_line_ids)

    def test_changed_setup_requires_refresh_and_history_stays(self):
        line, variants = self._variants()
        record = self._redemption(gift_choice_ids=[Command.create({
            'reward_line_id': line.id, 'product_id': variants[0].id})])
        extra = self.env['motogene.scratch.reward.product'].create({
            'pool_id': self.pool.id, 'variant_mode': 'choose',
            'product_tmpl_id': line.product_tmpl_id.id, 'quantity': 2})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()
        record.action_load_gift_choices()
        record.gift_choice_ids.write({'product_id': variants[0].id})
        record.action_confirm_redemption()
        snapshot = record.prize_snapshot
        extra.quantity = 9
        self.assertEqual(record.prize_snapshot, snapshot)
        self.assertEqual(sum(record.reward_line_ids.mapped('product_uom_qty')), 3)

    def test_wizard_variant_flow(self):
        line, variants = self._variants()
        wizard = self.env['motogene.scratch.redemption.wizard'].create({
            'sale_id': self.target_order.id, 'card_id': self.card.id,
            'card_photo': base64.b64encode(b'photo'),
        })
        self.assertEqual(wizard.gift_choice_ids.reward_line_id, line)
        self.assertEqual(set(wizard.gift_choice_ids.allowed_product_ids.ids), set(variants.ids))
        wizard.gift_choice_ids.product_id = variants[1]
        wizard.action_confirm()
        self.assertEqual(self.card.redemption_id.gift_choice_ids.product_id, variants[1])
        self.assertEqual(self.card.redemption_id.reward_line_ids.product_id, variants[1])

    def test_variant_cancel_preserves_history(self):
        line, variants = self._variants()
        record = self._redemption(gift_choice_ids=[Command.create({
            'reward_line_id': line.id, 'product_id': variants[0].id})])
        record.action_confirm_redemption()
        self.target_order.action_cancel()
        self.assertEqual(self.card.state, 'sent')
        self.assertEqual(record.state, 'cancelled')
        self.assertEqual(record.gift_choice_ids.product_id, variants[0])
        self.assertFalse(record.reward_line_ids)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.gift_choice_ids.unlink()

    def test_gift_variant_export_import_and_legacy_fixed(self):
        import json
        line, variants = self._variants()
        self.program.write({'scratch_vip_prefix': 'A', 'scratch_extra_prefix': 'A',
                            'scratch_package_line_ids': [Command.create({
                                'product_tmpl_id': self.product.product_tmpl_id.id,
                                'advertised_cards': 1, 'card_prefixes': 'A'})]})
        destination = self.env['motogene.promotion.program'].create({
            'name': 'Variant destination', 'reward_type': 'scratch_cards',
            'minimum_amount': 100, 'date_start': self.today, 'date_end': self.today})
        payload = self.program._scratch_setup_payload()
        self.assertEqual(payload['version'], 2)
        wizard = self.env['motogene.scratch.setup.transfer'].create({
            'program_id': destination.id, 'mode': 'import',
            'file_data': base64.b64encode(json.dumps(payload).encode())})
        wizard.action_import()
        imported = destination.scratch_serial_pool_ids.reward_product_line_ids
        self.assertEqual(imported.variant_mode, 'choose')
        self.assertEqual(imported.product_tmpl_id, line.product_tmpl_id)
        self.assertFalse(imported.product_id)
        payload['version'] = 1
        payload['pools'][0]['products'] = [{'product': self.program._scratch_setup_reference(variants[0]), 'quantity': 1}]
        wizard.file_data = base64.b64encode(json.dumps(payload).encode())
        wizard.action_import()
        imported = destination.scratch_serial_pool_ids.reward_product_line_ids
        self.assertEqual(imported.variant_mode, 'fixed')
        self.assertEqual(imported.product_id, variants[0])
    def test_delivery_uses_selected_variant(self):
        line, variants = self._variants()
        record = self._redemption(gift_choice_ids=[Command.create({
            'reward_line_id': line.id, 'product_id': variants[1].id})])
        record.action_confirm_redemption()
        self.target_order.action_confirm()
        self.assertEqual(self.target_order.state, 'sale')
        moves = self.target_order.picking_ids.move_ids.filtered(lambda m: m.product_id in variants)
        self.assertTrue(moves)
        self.assertEqual(moves.mapped('product_id'), variants[1])
        self.assertEqual(sum(moves.mapped('product_uom_qty')), 1)

    def test_company_filter_on_variant_options(self):
        line, variants = self._variants()
        foreign = self.env['res.company'].create({'name': 'Gift Variant Foreign Company'})
        line.product_tmpl_id.company_id = foreign
        self.assertFalse(line._gift_variants(self.target_order.company_id))
        record = self._redemption(gift_choice_ids=[Command.create({
            'reward_line_id': line.id, 'product_id': variants[0].id})])
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()
        self.assertEqual(self.card.state, 'sent')
