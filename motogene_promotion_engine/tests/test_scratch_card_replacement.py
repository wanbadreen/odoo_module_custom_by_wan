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

    def _recall(self, **overrides):
        target = self.picking.copy({'sale_id': self.order.id})
        values = {'replacement_mode': 'courier_recall', 'replacement_picking_id': target.id,
                  'recall_requested': True, 'not_handed_over': False, 'reason': 'courier'}
        values.update(overrides)
        wizard = self._wizard(**values)
        wizard.action_confirm()
        return self.picking.scratch_replacement_history_ids[:1], target

    def test_courier_recall_moves_entitlement_and_holds_original(self):
        history, target = self._recall()
        self.assertEqual(history.original_status, 'pending')
        self.assertEqual(history.picking_id, self.picking)
        self.assertEqual(history.replacement_picking_id, target)
        self.assertEqual(self.card.picking_id, target)
        self.assertEqual(self.card.state, 'reserved')
        self.assertFalse(self.picking.scratch_card_line_ids)
        self.assertEqual(len(self.order.scratch_card_line_ids.filtered(lambda c: c.state != 'released')), 1)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.Card.create({'sale_id': self.order.id, 'program_id': self.program.id,
                              'prefix': 'A', 'serial_number': history.old_serial})
        target.move_ids.write({'quantity': 1, 'picked': True})
        target._action_done()
        self.assertEqual(self.card.state, 'sent')

    def test_courier_original_released_only_after_physical_inspection(self):
        history, target = self._recall()
        before = self.pool.next_number
        for received, unscratched in ((False, False), (True, False), (False, True)):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                history._resolve_courier_return('reuse', received, unscratched, 'Inspection')
        self.assertEqual(history.original_status, 'pending')
        history._resolve_courier_return('reuse', True, True, 'Courier returned sealed parcel; card inspected')
        self.assertEqual(history.original_status, 'reusable')
        self.assertEqual(history.recovered_card_id.state, 'released')
        self.assertFalse(history.recovered_card_id.undelivered_released_at)
        self.assertEqual(self.card.picking_id, target)
        self.assertEqual(self.card.serial_number, 'A991002')
        self.assertEqual(self.pool.next_number, before)
        self.assertEqual(self.pool.reserve_serial(), 'A991001')
        self.assertEqual(self.pool.next_number, before)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            history._resolve_courier_return('reuse', True, True, 'Duplicate click')

    def test_courier_return_bad_or_lost_card_stays_permanently_blocked(self):
        history, target = self._recall()
        history._resolve_courier_return('block', True, False, 'Returned card was scratched')
        self.assertEqual(history.original_status, 'blocked')
        self.assertFalse(history.recovered_card_id)
        self.assertEqual(self.pool.reserve_serial(), 'A991003')
        with self.assertRaises(UserError), self.env.cr.savepoint():
            history._resolve_courier_return('reuse', True, True, 'Cannot undo permanent blocking')

    def test_courier_recall_rejects_wrong_target_or_missing_confirmation(self):
        target = self.picking.copy({'sale_id': self.order.id})
        for overrides in ({'recall_requested': False},):
            vals = {'replacement_mode': 'courier_recall', 'replacement_picking_id': target.id,
                    'recall_requested': True, 'not_handed_over': False}
            vals.update(overrides)
            with self.assertRaises(UserError), self.env.cr.savepoint():
                self._wizard(**vals).action_confirm()
        self.assertEqual(self.pool.next_number, 991002)
        self.assertEqual(self.card.serial_number, 'A991001')

    def test_reissued_original_can_have_new_replacement_history(self):
        history, target = self._recall()
        history._resolve_courier_return('reuse', True, True, 'Received in good condition')
        serial = self.pool.reserve_serial()
        reused = self.Card.create({'sale_id': self.order.id, 'picking_id': target.id,
                                  'program_id': self.program.id, 'prefix': 'A',
                                  'serial_number': serial, 'state': 'reserved'})
        reused._replace_scratch_card('printing', 'Printing defect discovered later', True, serial)
        histories = self.env['motogene.scratch.card.replacement'].search([('old_serial', '=', serial)])
        self.assertEqual(len(histories), 2)
        self.assertEqual(set(histories.mapped('original_status')), {'reusable', 'blocked'})
        self.assertEqual(history.original_status, 'reusable')

    def test_receipt_popup_opens_empty_and_permanent_replacements_cannot_be_reused(self):
        history, target = self._recall()
        action = history.action_receive_recalled_card()
        receipt = self.env[action['res_model']].browse(action['res_id'])
        self.assertEqual(receipt.history_id, history)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            receipt.action_confirm()
        receipt.write({'physically_received': True, 'unscratched': True, 'notes': 'Received and checked'})
        receipt.action_confirm()
        self.assertEqual(history.original_status, 'reusable')
        with self.assertRaises(UserError), self.env.cr.savepoint():
            history.action_receive_recalled_card()

    def test_recall_rejects_another_sales_order_delivery(self):
        other_order = self.env['sale.order'].create({'partner_id': self.partner.id})
        wrong = self.env['stock.picking'].create({
            'sale_id': other_order.id, 'partner_id': self.partner.id,
            'picking_type_id': self.picking.picking_type_id.id,
            'location_id': self.picking.location_id.id,
            'location_dest_id': self.picking.location_dest_id.id,
        })
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._wizard(replacement_mode='courier_recall', replacement_picking_id=wrong.id,
                         recall_requested=True, not_handed_over=False).action_confirm()
        self.assertEqual(self.card.picking_id, self.picking)
        self.assertEqual(self.pool.next_number, 991002)

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

    def test_open_empty_popup_then_require_input_at_confirmation(self):
        action = self.picking.action_replace_scratch_card()
        wizard = self.env[action['res_model']].browse(action['res_id'])
        self.assertEqual(wizard.picking_id, self.picking)
        self.assertFalse(wizard.card_id)
        self.assertFalse(wizard.reason)
        self.assertFalse(wizard.notes)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_confirm()
        wizard.card_id = self.card
        wizard._onchange_card_id()
        self.assertEqual(wizard.serial_to_replace, self.card.serial_number)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_confirm()
        wizard.write({'reason': 'damaged', 'notes': 'Already scratched before handover', 'not_handed_over': True})
        wizard.action_confirm()
        self.assertEqual(self.card.serial_number, 'A991002')


    def test_courier_recall_can_keep_original_delivery(self):
        for explicit_original in (False, True):
            with self.env.cr.savepoint():
                old_serial = self.card.serial_number
                wizard = self._wizard(
                    replacement_mode='courier_recall', recall_requested=True,
                    not_handed_over=False,
                    replacement_picking_id=self.picking.id if explicit_original else False)
                wizard.action_confirm()
                history = self.picking.scratch_replacement_history_ids[:1]
                self.assertEqual(history.old_serial, old_serial)
                self.assertEqual(history.original_status, 'pending')
                self.assertEqual(history.picking_id, self.picking)
                self.assertEqual(history.replacement_picking_id, self.picking)
                self.assertEqual(self.card.picking_id, self.picking)
                self.assertEqual(self.card.state, 'sent')
                self.assertEqual(self.picking.state, 'done')
                self.assertNotEqual(self.card.serial_number, old_serial)
                history._resolve_courier_return('reuse', True, True, 'Original card physically received')
                self.assertEqual(history.original_status, 'reusable')
                self.assertEqual(history.recovered_card_id.state, 'released')
                self.assertEqual(len(self.order.scratch_card_line_ids.filtered(lambda c: c.state != 'released')), 1)
