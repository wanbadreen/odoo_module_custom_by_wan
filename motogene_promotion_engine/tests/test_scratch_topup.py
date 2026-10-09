import json
from .test_scratch_alternative import TestScratchAlternative
from odoo.exceptions import UserError
from odoo.tests.common import tagged


@tagged('post_install', '-at_install')
class TestScratchTopup(TestScratchAlternative):
    def test_selected_types_and_vip_preserved(self):
        self.program.write({'scratch_extra_allowed_prefixes': 'D,B,G', 'scratch_vip_prefix': 'A'})
        self.order.write({'scratch_vip_cards': 1, 'scratch_total_cards': 4,
                          'scratch_topup_selection': json.dumps({'D': 1, 'B': 1, 'G': 1})})
        self.assertCountEqual(self.order._scratch_order_types(), ['A', 'D', 'B', 'G'])
        self.order._allocate_scratch_cards()
        ids = self.order.scratch_card_line_ids.ids
        self.order._allocate_scratch_cards()
        self.assertEqual(self.order.scratch_card_line_ids.ids, ids)
        self.program.scratch_extra_allowed_prefixes = 'D'
        self.assertCountEqual(self.order._scratch_order_types(), ['A', 'D', 'B', 'G'])

    def test_wrong_quantity_rejected_before_reservation(self):
        self.order.scratch_topup_selection = json.dumps({'D': 4})
        with self.assertRaises(UserError):
            self.order._scratch_order_types()
        self.assertFalse(self.order.scratch_card_line_ids)

    def test_available_balance_reserves_fixed_vip_requirement(self):
        self.program.scratch_vip_prefix = 'B'
        self.order.write({'scratch_vip_cards': 1, 'scratch_total_cards': 4})
        wizard = self.env['motogene.scratch.topup.wizard'].create({
            'sale_id': self.order.id, 'program_id': self.program.id,
            'extra_quantity': 3, 'vip_quantity': 1})
        self.assertEqual(wizard._available(self.pools['B']), 2)

    def test_relational_choices_read_existing_settings(self):
        self.program.write({'scratch_vip_prefix': 'A', 'scratch_extra_prefix': 'D',
                            'scratch_extra_allowed_prefixes': 'D,B,G'})
        self.assertEqual(self.program.scratch_vip_pool_id, self.pools['A'])
        self.assertEqual(self.program.scratch_extra_pool_id, self.pools['D'])
        self.assertEqual(set(self.program.scratch_extra_allowed_pool_ids.mapped('prefix')), {'D', 'B', 'G'})
        self.program.scratch_extra_allowed_pool_ids = self.pools['D'] | self.pools['B']
        self.assertEqual(set(self.program._scratch_topup_prefixes()), {'D', 'B'})
        self.program.scratch_extra_pool_id = self.pools['B']
        self.assertEqual(self.program.scratch_extra_prefix, 'B')

    def test_missing_pool_blocks_activation(self):
        from odoo.exceptions import ValidationError
        self.program.scratch_vip_prefix = 'Z'
        with self.assertRaises(ValidationError):
            self.program._validate_scratch_pool_setup()
