from collections import Counter
from datetime import timedelta
from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from ..models.scratch_card import ScratchSerialShortage


@tagged('post_install', '-at_install')
class TestScratchAlternative(TransactionCase):
    def setUp(self):
        super().setUp()
        today = fields.Date.context_today(self.env['sale.order'])
        self.program = self.env['motogene.promotion.program'].create({
            'name': 'Alternative allocation test', 'reward_type': 'scratch_cards',
            'minimum_amount': 100, 'scratch_extra_prefix': 'D',
            'date_start': today, 'date_end': today,
            'scratch_redemption_expiry_date': today + timedelta(days=30),
            'company_id': self.env.company.id,
        })
        self.pools = {}
        for prefix, size in [('D', 1), ('B', 3), ('G', 3), ('A', 1)]:
            self.pools[prefix] = self.env['motogene.scratch.serial.pool'].create({
                'program_id': self.program.id, 'prefix': prefix,
                'first_number': 999001, 'last_number': 999000 + size,
            })
        partner = self.env['res.partner'].create({'name': 'Alternative customer'})
        self.order = self.env['sale.order'].create({'partner_id': partner.id})
        self.order.write({'state': 'sale', 'scratch_program_id': self.program.id,
                          'scratch_base_cards': 3, 'scratch_total_cards': 3})

    def _choices(self, prefixes):
        return [{'sale_id': self.order.id, 'original_prefix': 'D',
                 'pool_id': self.pools[p].id, 'quantity': 1} for p in prefixes]

    def test_originals_kept_and_reconciliation_idempotent(self):
        self.order._allocate_scratch_cards(allow_partial=True)
        self.order._allocate_scratch_alternatives(self._choices(['B', 'G']))
        cards = self.order.scratch_card_line_ids
        self.assertEqual(Counter(cards.mapped('prefix')), Counter(['D', 'B', 'G']))
        self.assertEqual(cards.filtered('original_prefix').mapped('original_prefix'), ['D', 'D'])
        ids = cards.ids
        self.program.scratch_replacement_policy = 'none'
        self.order._allocate_scratch_cards()
        self.assertEqual(self.order.scratch_card_line_ids.ids, ids)
        self.assertEqual(self.order.scratch_pending_cards, 0)
        self.assertEqual(cards.filtered('original_prefix').mapped('replacement_by_id'), self.env.user)

    def test_allowed_policy_and_live_change(self):
        self.order._allocate_scratch_cards(allow_partial=True)
        self.program.scratch_replacement_policy = 'allowed'
        self.pools['D'].allowed_replacement_pool_ids = [Command.set(self.pools['B'].ids)]
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.order._allocate_scratch_alternatives(self._choices(['B', 'G']))
        self.pools['D'].allowed_replacement_pool_ids = [Command.set((self.pools['B'] | self.pools['G']).ids)]
        self.order._allocate_scratch_alternatives(self._choices(['B', 'G']))
        self.assertEqual(len(self.order.scratch_card_line_ids), 3)

    def test_no_replacement_and_overallocation_rejected(self):
        self.order._allocate_scratch_cards(allow_partial=True)
        self.program.scratch_replacement_policy = 'none'
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.order._allocate_scratch_alternatives(self._choices(['B', 'G']))
        self.program.scratch_replacement_policy = 'any'
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.order._allocate_scratch_alternatives(self._choices(['B', 'G', 'B']))
        self.assertEqual(len(self.order.scratch_card_line_ids), 1)

    def test_exhaustion_rolls_back_replacement_batch(self):
        self.order._allocate_scratch_cards(allow_partial=True)
        # Reserve all G stock first, representing another confirmed order.
        for i in range(3):
            self.env['motogene.scratch.picking.card'].create({
                'sale_id': self.order.id, 'program_id': self.program.id,
                'prefix': 'G', 'serial_number': self.pools['G'].reserve_serial(),
                'state': 'void',
            })
        other = self.env['sale.order'].create({'partner_id': self.order.partner_id.id})
        self.order.scratch_card_line_ids.filtered(lambda c: c.prefix == 'G').write({'sale_id': other.id})
        before = self.pools['B'].next_number
        with self.assertRaises(ScratchSerialShortage), self.env.cr.savepoint():
            self.order._allocate_scratch_alternatives(self._choices(['B', 'G']))
        self.assertEqual(self.pools['B'].next_number, before)
        self.assertEqual(len(self.order.scratch_card_line_ids), 1)

    def test_vip_original_preserved_before_replacement(self):
        self.order.write({'scratch_vip_cards': 1, 'scratch_total_cards': 4,
                          'scratch_base_cards': 3})
        self.program.scratch_vip_prefix = 'A'
        self.order._allocate_scratch_cards(allow_partial=True)
        self.assertEqual(Counter(self.order.scratch_card_line_ids.mapped('prefix')), Counter(['A', 'D']))
        self.order._allocate_scratch_alternatives(self._choices(['B', 'G']))
        self.order._allocate_scratch_cards()
        self.assertEqual(len(self.order.scratch_card_line_ids), 4)

    def test_other_promotion_disallowed(self):
        self.order._allocate_scratch_cards(allow_partial=True)
        other = self.program.copy({'name': 'Other company campaign', 'scratch_serial_pool_ids': []})
        pool = self.pools['B'].copy({'program_id': other.id})
        choices = self._choices(['B', 'G'])
        choices[0]['pool_id'] = pool.id
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.order._allocate_scratch_alternatives(choices)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.pools['D'].allowed_replacement_pool_ids = [Command.set(pool.ids)]

    def _quotation(self):
        self.env['motogene.promotion.program'].search([
            ('reward_type', '=', 'scratch_cards'), ('state', '=', 'active')
        ]).write({'state': 'draft'})
        product = self.env['product.product'].create({'name': 'Alternative test package', 'type': 'service'})
        self.program.write({'scratch_package_line_ids': [Command.create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'advertised_cards': 3, 'card_prefixes': 'D,D,D',
        })]})
        self.program.action_activate()
        return self.env['sale.order'].create({
            'partner_id': self.order.partner_id.id,
            'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': 1, 'price_unit': 300})],
        })

    def _open_alternative(self, order):
        action = order.action_confirm()
        self.assertEqual(order.state, 'draft')
        shortage = self.env[action['res_model']].browse(action['res_id'])
        action = shortage.action_alternative_cards()
        return self.env[action['res_model']].browse(action['res_id'])

    def test_wizard_confirm_and_quote_cancel_release(self):
        order = self._quotation()
        wizard = self._open_alternative(order)
        self.assertEqual(len(wizard.line_ids), 1)
        self.assertEqual(wizard.line_ids.quantity, 2)
        wizard.line_ids.replacement_pool_id = self.pools['B']
        wizard.line_ids.action_split()
        self.assertEqual(len(wizard.line_ids), 2)
        wizard.line_ids.filtered(lambda l: not l.replacement_pool_id).replacement_pool_id = self.pools['G']
        wizard.action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertEqual(Counter(order.scratch_card_line_ids.mapped('prefix')), Counter(['D', 'B', 'G']))
        order.action_cancel()
        self.assertTrue(all(c.state == 'released' for c in order.scratch_card_line_ids))

    def test_wizard_rechecks_policy_and_keeps_quote_draft(self):
        order = self._quotation()
        wizard = self._open_alternative(order)
        wizard.line_ids.replacement_pool_id = self.pools['B']
        self.program.scratch_replacement_policy = 'none'
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_confirm()
        self.assertEqual(order.state, 'draft')
        self.assertFalse(order.scratch_card_line_ids)
        self.assertEqual(self.pools['D'].next_number, self.pools['D'].first_number)

    def test_transfer_includes_policy_and_allowed_prefixes(self):
        import base64
        import json
        product = self.env['product.product'].create({'name': 'Alternative transfer gift', 'type': 'service'})
        self.program.write({'scratch_vip_prefix': 'A', 'scratch_replacement_policy': 'allowed',
                            'scratch_package_line_ids': [Command.create({
                                'product_tmpl_id': product.product_tmpl_id.id,
                                'advertised_cards': 1, 'card_prefixes': 'D'})]})
        for pool in self.pools.values():
            pool.write({'redemption_reward_type': 'products', 'reward_product_line_ids': [Command.create({
                'product_id': product.id, 'quantity': 1})]})
        self.pools['D'].allowed_replacement_pool_ids = [Command.set((self.pools['B'] | self.pools['G']).ids)]
        destination = self.env['motogene.promotion.program'].create({
            'name': 'Alternative transfer destination', 'reward_type': 'scratch_cards',
            'minimum_amount': 100, 'date_start': self.program.date_start, 'date_end': self.program.date_end})
        payload = self.program._scratch_setup_payload()
        wizard = self.env['motogene.scratch.setup.transfer'].create({
            'program_id': destination.id, 'mode': 'import',
            'file_data': base64.b64encode(json.dumps(payload).encode())})
        wizard.action_import()
        self.assertEqual(destination.scratch_replacement_policy, 'allowed')
        pool = destination.scratch_serial_pool_ids.filtered(lambda p: p.prefix == 'D')
        self.assertEqual(set(pool.allowed_replacement_pool_ids.mapped('prefix')), {'B', 'G'})
        self.assertTrue(all(p.program_id == destination for p in pool.allowed_replacement_pool_ids))
        # Old JSON files remain supported.
        payload['settings'].pop('scratch_replacement_policy')
        for entry in payload['pools']:
            entry.pop('allowed_replacement_prefixes')
        wizard.file_data = base64.b64encode(json.dumps(payload).encode())
        wizard.action_import()
        self.assertEqual(destination.scratch_replacement_policy, 'any')
        self.assertFalse(pool.allowed_replacement_pool_ids)

    def test_available_count_with_cold_orm_cache(self):
        pool = self.pools['B']
        pool.flush_recordset()
        pool.invalidate_recordset(['next_number', 'last_number'])
        self.assertEqual(pool._scratch_available_count(), 3)
        pool.reserve_serial()
        pool.invalidate_recordset(['next_number', 'last_number'])
        self.assertEqual(pool._scratch_available_count(), 2)
