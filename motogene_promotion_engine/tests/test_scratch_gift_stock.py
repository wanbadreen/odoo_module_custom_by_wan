from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .test_scratch_gift_variants import TestScratchGiftVariants


@tagged('post_install', '-at_install')
class TestScratchGiftStock(TestScratchGiftVariants):
    def _stock_variants(self, blue=2, orange=0):
        line, variants = self._variants()
        line.product_tmpl_id.is_storable = True
        warehouse = self.target_order.warehouse_id
        for product, quantity in zip(variants, [blue, orange]):
            if quantity:
                self.env['stock.quant']._update_available_quantity(
                    product, warehouse.lot_stock_id, quantity)
        return line, variants

    def _variant_record(self, line, variant, order=None, card=None):
        return self._redemption(
            target_sale_id=(order or self.target_order).id,
            card_id=(card or self.card).id, card_photo=False,
            gift_choice_ids=[Command.create({
                'reward_line_id': line.id, 'product_id': variant.id})])

    def test_popup_lists_stock_and_excludes_unavailable_choice(self):
        line, variants = self._stock_variants()
        wizard = self.env['motogene.scratch.redemption.wizard'].create({
            'sale_id': self.target_order.id, 'card_id': self.card.id})
        balances = self.target_order._scratch_gift_stock(variants)
        self.assertEqual(balances, {variants[0].id: 2, variants[1].id: 0})
        self.assertIn(variants[0], wizard.single_allowed_variant_ids)
        self.assertNotIn(variants[1], wizard.single_allowed_variant_ids)
        self.assertIn('Insufficient stock', str(wizard.gift_stock_html))
        self.assertIn(self.target_order.warehouse_id.display_name, str(wizard.gift_stock_html))

    def test_stock_change_rechecked_before_redeem(self):
        line, variants = self._stock_variants(blue=1)
        record = self._variant_record(line, variants[0])
        self.env['stock.quant']._update_available_quantity(
            variants[0], self.target_order.warehouse_id.lot_stock_id, -1)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()
        self.assertEqual(record.state, 'draft')
        self.assertEqual(self.card.state, 'sent')
        self.assertFalse(record.reward_line_ids)

    def test_draft_gift_promise_blocks_last_unit_and_cancel_restores_it(self):
        line, variants = self._stock_variants(blue=1)
        first = self._variant_record(line, variants[0])
        first.action_confirm_redemption()
        self.assertEqual(self.target_order._scratch_gift_stock(variants)[variants[0].id], 0)
        other_order = self.env['sale.order'].create({'partner_id': self.partner.id})
        other_card = self.card.copy({'serial_number': 'STOCK-TEST-A2', 'state': 'sent'})
        second = self._variant_record(line, variants[0], other_order, other_card)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            second.action_confirm_redemption()
        self.target_order.action_cancel()
        self.assertEqual(other_order._scratch_gift_stock(variants)[variants[0].id], 1)
        second.action_confirm_redemption()
        self.assertEqual(second.state, 'confirmed')

    def test_reserved_and_dispatched_gifts_are_not_double_deducted(self):
        line, variants = self._stock_variants(blue=3)
        record = self._variant_record(line, variants[0])
        record.action_confirm_redemption()
        self.assertEqual(self.target_order._scratch_gift_stock(variants)[variants[0].id], 2)
        self.target_order.action_confirm()
        deliveries = self.target_order.picking_ids.filtered(lambda p: p.state != 'cancel')
        deliveries.action_assign()
        self.assertEqual(self.target_order._scratch_gift_stock(variants)[variants[0].id], 2)
        for picking in deliveries:
            for move in picking.move_ids:
                move.write({'quantity': move.product_uom_qty, 'picked': True})
            picking._action_done()
        self.assertEqual(self.target_order._scratch_gift_stock(variants)[variants[0].id], 2)

    def test_other_warehouse_stock_does_not_qualify(self):
        line, variants = self._stock_variants(blue=0)
        other = self.env['stock.warehouse'].create({
            'name': 'Gift Other Warehouse', 'code': 'GSO',
            'company_id': self.target_order.company_id.id})
        self.env['stock.quant']._update_available_quantity(variants[0], other.lot_stock_id, 10)
        self.assertEqual(self.target_order._scratch_gift_stock(variants)[variants[0].id], 0)
        self.target_order.warehouse_id = other
        self.assertEqual(self.target_order._scratch_gift_stock(variants)[variants[0].id], 10)

    def test_untracked_variant_remains_usable(self):
        line, variants = self._variants()
        self.assertIsNone(self.target_order._scratch_gift_stock(variants)[variants[0].id])
        record = self._variant_record(line, variants[0])
        record.action_confirm_redemption()
        self.assertEqual(record.state, 'confirmed')

    def test_combined_rows_for_same_variant_must_fit_stock(self):
        line, variants = self._stock_variants(blue=1)
        extra = self.env['motogene.scratch.reward.product'].create({
            'pool_id': self.pool.id, 'variant_mode': 'choose',
            'product_tmpl_id': line.product_tmpl_id.id, 'quantity': 1})
        record = self._variant_record(line, variants[0])
        record.gift_choice_ids = [Command.create({
            'reward_line_id': extra.id, 'product_id': variants[0].id})]
        with self.assertRaises(UserError), self.env.cr.savepoint():
            record.action_confirm_redemption()
        self.assertEqual(self.card.state, 'sent')
