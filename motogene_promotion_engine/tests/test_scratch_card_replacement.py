from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged, new_test_user


@tagged('post_install', '-at_install')
class TestScratchCardReplacement(TransactionCase):
    def setUp(self):
        super().setUp()
        self.partner = self.env['res.partner'].create({'name': 'Card replacement customer'})
        self.product = self.env['product.product'].create({'name': 'Replacement test box', 'type': 'consu'})
        self.order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_uom_qty': 1, 'price_unit': 100})],
        })
        self.order.action_confirm()
        self.picking = self.order.picking_ids[:1]
        self.assertTrue(self.picking)
        self.picking.move_ids.write({'quantity': 1, 'picked': True})
        self.picking._action_done()
        today = fields.Date.today()
        self.program = self.env['motogene.promotion.program'].create({
            'name': 'Replacement test promo', 'reward_type': 'scratch_cards', 'minimum_amount': 100,
            'company_id': self.order.company_id.id, 'date_start': today, 'date_end': today,
            'scratch_redemption_expiry_date': today + timedelta(days=30),
        })
        self.pool = self.env['motogene.scratch.serial.pool'].create({
            'program_id': self.program.id, 'prefix': 'A', 'first_number': 991001, 'last_number': 991010,
        })
        self.Card = self.env['motogene.scratch.picking.card']
        self.card = self.Card.create({
            'sale_id': self.order.id, 'picking_id': self.picking.id, 'program_id': self.program.id,
            'prefix': 'A', 'serial_number': self.pool.reserve_serial(), 'state': 'sent',
        })

    def _wizard(self, **overrides):
        vals = {'picking_id': self.picking.id, 'card_id': self.card.id,
                'serial_to_replace': self.card.serial_number, 'reason': 'damaged',
                'notes': 'Already scratched before packing', 'not_handed_over': True}
        vals.update(overrides)
        return self.env['motogene.scratch.replace.wizard'].create(vals)

    def test_replacement_preserves_entitlement_delivery_expiry_and_audit(self):
        moves, expiry = self.picking.move_ids.ids, self.card.expiry_date
        before = self.Card.search([('sale_id', '=', self.order.id)]).ids
        self._wizard().action_confirm()
        self.assertEqual(self.card.serial_number, 'A991002')
        self.assertEqual(self.card.state, 'sent')
        self.assertEqual(self.card.expiry_date, expiry)
        self.assertEqual(self.picking.state, 'done')
        self.assertEqual(self.picking.move_ids.ids, moves)
        self.assertEqual(self.Card.search([('sale_id', '=', self.order.id)]).ids, before)
        self.assertFalse(self.Card.search([('serial_number', '=', 'A991001')]))
        history = self.picking.scratch_replacement_history_ids
        self.assertEqual(history, self.order.scratch_replacement_history_ids)
        self.assertEqual((history.old_serial, history.new_serial), ('A991001', 'A991002'))
        self.assertEqual(self.pool.next_number, 991003)
        self.assertEqual(self.pool._scratch_available_count(), 8)

    def test_repeated_popup_rejected_but_new_replacement_allowed(self):
        wizard = self._wizard()
        wizard.action_confirm()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_confirm()
        self._wizard(reason='missing').action_confirm()
        self.assertEqual(self.card.serial_number, 'A991003')
        self.assertEqual(len(self.picking.scratch_replacement_history_ids), 2)

    def test_handover_reason_and_notes_required(self):
        for overrides in ({'not_handed_over': False}, {'notes': '   '}):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                self._wizard(**overrides).action_confirm()
        self.assertEqual(self.card.serial_number, 'A991001')
        self.assertEqual(self.pool.next_number, 991002)

    def test_exhaustion_does_not_change_card_or_counter(self):
        self.pool.last_number = 991001
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._wizard().action_confirm()
        self.assertEqual(self.card.serial_number, 'A991001')
        self.assertEqual(self.pool.next_number, 991002)
        self.assertFalse(self.picking.scratch_replacement_history_ids)

    def test_blocked_serial_cannot_be_created_or_restored(self):
        self._wizard().action_confirm()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.card.copy({'sale_id': self.order.id, 'serial_number': 'A991001'})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.card.write({'serial_number': 'A991001'})
        history = self.picking.scratch_replacement_history_ids
        with self.assertRaises(UserError), self.env.cr.savepoint():
            history.sudo().write({'old_serial': 'A991009'})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            history.sudo().unlink()

    def test_existing_redemption_blocks_replacement(self):
        target = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.env['motogene.scratch.redemption'].create({'card_id': self.card.id, 'target_sale_id': target.id})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._wizard().action_confirm()
        self.assertEqual(self.pool.next_number, 991002)

    def test_void_released_redeemed_and_wrong_delivery_rejected(self):
        wizard = self._wizard()
        wizard.picking_id = self.picking.copy()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_confirm()
        self.card.state = 'void'
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._wizard().action_confirm()

    def test_operations_access_required(self):
        user = new_test_user(self.env, login='scratch_replace_no_stock', groups='base.group_user')
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.card.with_user(user)._replace_scratch_card('missing', 'Missing before dispatch', True, self.card.serial_number)

    def test_legacy_entry_issues_no_card_and_does_not_advance_counter(self):
        # Reproduce the one-off shell fix before the new history feature exists.
        replacement = self.pool.reserve_serial()
        self.card.write({'serial_number': replacement})
        self.assertEqual(self.pool.next_number, 991003)
        history = self.card.sudo()._record_legacy_replacement('A991001', 'damaged', 'Previously replaced through shell')
        self.assertTrue(history.legacy_entry)
        self.assertEqual(history.new_serial, 'A991002')
        self.assertEqual(self.pool.next_number, 991003)
        self.assertEqual(len(self.picking.scratch_card_line_ids), 1)

    def test_reusable_serial_precedes_counter_but_never_blocked_original(self):
        self._wizard().action_confirm()
        reusable = self.Card.create({
            'sale_id': self.order.id, 'program_id': self.program.id, 'prefix': 'A',
            'serial_number': 'A991010', 'state': 'released',
        })
        self.assertEqual(self.pool.reserve_serial(), reusable.serial_number)
        self.assertEqual(self.pool.next_number, 991003)

    def test_unvalidated_delivery_keeps_replacement_allocated(self):
        pending = self.picking.copy({'sale_id': self.order.id})
        card = self.Card.create({
            'sale_id': self.order.id, 'picking_id': pending.id, 'program_id': self.program.id,
            'prefix': 'A', 'serial_number': self.pool.reserve_serial(), 'state': 'reserved',
            'original_prefix': 'B',
        })
        before = card.serial_number
        card._replace_scratch_card('printing', 'Unreadable serial', True, before)
        self.assertEqual(card.state, 'reserved')
        self.assertEqual(card.original_prefix, 'B')
        self.assertEqual(card.serial_number, 'A991003')
        self.assertNotEqual(pending.state, 'done')

    def test_pending_physical_return_blocks_replacement(self):
        returned = self.picking.copy({
            'sale_id': self.order.id, 'return_id': self.picking.id,
            'location_id': self.picking.location_dest_id.id,
            'location_dest_id': self.picking.location_id.id,
        })
        returned.scratch_return_card_ids = [Command.set(self.card.ids)]
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._wizard().action_confirm()
        self.assertEqual(self.card.serial_number, 'A991001')
