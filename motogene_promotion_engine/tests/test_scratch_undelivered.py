from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import tagged
from .test_scratch_returns import TestScratchCardReturns


@tagged('post_install', '-at_install')
class TestScratchUndelivered(TestScratchCardReturns):
    def _wizard(self, cards=None):
        return self.env['motogene.scratch.undelivered.wizard'].create({
            'picking_id': self.delivery.id,
            'card_ids': [Command.set((cards if cards is not None else self.cards[:1]).ids)],
            'reason': 'Cancelled before courier collection',
            'not_handed_over': True, 'cards_unscratched': True,
        })

    def test_release_audit_and_repeated_submission(self):
        wizard = self._wizard()
        wizard.action_confirm()
        self.assertEqual(self.cards[0].state, 'released')
        self.assertEqual(self.cards[1].state, 'sent')
        self.assertEqual(self.cards[0].undelivered_released_by_id, self.env.user)
        self.assertTrue(self.cards[0].undelivered_released_at)
        self.assertEqual(self.cards[0].picking_id, self.delivery)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_confirm()

    def test_required_physical_confirmations(self):
        wizard = self._wizard()
        wizard.cards_unscratched = False
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_confirm()
        self.assertEqual(self.cards[0].state, 'sent')

    def test_wrong_delivery_and_void_rejected(self):
        other = self._picking()
        other._action_done()
        wizard = self._wizard()
        wizard.picking_id = other
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_confirm()
        self.cards[0].write({'state': 'void'})
        wizard.picking_id = self.delivery
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_confirm()

    def test_pending_physical_return_blocks_release(self):
        returned = self._picking(return_of=self.delivery)
        returned.scratch_return_card_ids = [Command.set(self.cards[:1].ids)]
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._wizard().action_confirm()
        self.assertEqual(self.cards[0].state, 'sent')

    def test_recovered_serial_reused_before_next_number(self):
        self.cards[0].write({'serial_number': 'A999001'})
        pool = self.env['motogene.scratch.serial.pool'].create({
            'program_id': self.program.id, 'prefix': 'A',
            'first_number': 999001, 'last_number': 999010, 'next_number': 999006,
        })
        self._wizard().action_confirm()
        self.assertEqual(pool.reserve_serial(), 'A999001')
        self.assertEqual(pool.next_number, 999006)
