"""Run only in an isolated Odoo 18 test DB. These test ORM integration, not HTTP auth."""
from datetime import timedelta
from uuid import uuid4
from odoo import fields
from odoo.tests.common import TransactionCase, tagged, new_test_user
from odoo.addons.morimoto_mobile.contract import quotation_payload
from odoo.addons.morimoto_mobile.controllers import Mobile
from werkzeug.exceptions import Conflict


@tagged('post_install', '-at_install', 'morimoto_mobile_integration')
class TestMobilePromotionIntegration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tester = new_test_user(
            cls.env, login='morimoto-integration-tester',
            groups='base.group_user,sales_team.group_sale_salesman,morimoto_mobile.group_mobile_api',
        )
        cls.partner = cls.env['res.partner'].create({
            'name': 'Mobile test customer', 'user_id': cls.tester.id,
        })
        cls.box = cls.env['product.product'].create({
            'name': 'Mobile integration box', 'list_price': 100,
            'type': 'consu', 'sale_ok': True, 'morimoto_mobile_enabled': True,
        })
        cls.gift = cls.env['product.product'].create({
            'name': 'Mobile integration gift', 'list_price': 10,
            'type': 'consu', 'sale_ok': True, 'morimoto_mobile_enabled': False,
        })
        today = fields.Date.today()
        cls.program = cls.env['motogene.promotion.program'].create({
            'name': 'Mobile integration: every 3 boxes get 2',
            'company_id': cls.env.company.id, 'state': 'active',
            'date_start': today - timedelta(days=1), 'date_end': today + timedelta(days=1),
            'threshold_qty': 3, 'reward_qty': 2, 'repeat_reward': True,
            'reward_product_id': cls.gift.id,
            'eligibility_line_ids': [(0, 0, {'product_tmpl_id': cls.box.product_tmpl_id.id, 'box_units_per_qty': 1})],
        })

    def _create(self, quantity):
        return self.env['sale.order'].with_user(self.tester).with_context(
            tracking_disable=True, mail_create_nosubscribe=True,
        ).create({
            'partner_id': self.partner.id, 'company_id': self.env.company.id,
            'pricelist_id': self.partner.property_product_pricelist.id,
            'morimoto_mobile_key': str(uuid4()),
            'order_line': [(0, 0, {'product_id': self.box.id, 'product_uom_qty': quantity})],
        })

    def test_nested_create_and_gift_serialization_as_service_user(self):
        for quantity, expected in [(2, 0), (3, 2), (5, 2), (6, 4), (9, 6)]:
            with self.subTest(quantity=quantity):
                order = self._create(quantity)
                reward = order.order_line.filtered(lambda line: line.promotion_program_id == self.program)
                self.assertEqual(sum(reward.mapped('product_uom_qty')), expected)
                self.assertEqual(order.state, 'draft')
                self.assertTrue(all(line.price_unit == 0 for line in reward))
                payload = quotation_payload(order)
                returned = [line for line in payload['lines'] if line['is_promotion_reward'] and 'Mobile integration: every 3 boxes get 2' in line['name']]
                self.assertEqual(sum(line['quantity'] for line in returned), expected)
                order._apply_motogene_promotions()
                self.assertEqual(len(order.order_line.filtered(lambda line: line.promotion_program_id == self.program)), int(expected > 0))

    def test_excluded_customer_does_not_receive_gift(self):
        tag = self.env['res.partner.category'].create({'name': 'Mobile exclusion fixture'})
        self.partner.category_id = tag
        self.program.excluded_partner_tag_ids = tag
        order = self._create(6)
        self.assertFalse(order.order_line.filtered(lambda line: line.promotion_program_id == self.program))

    def test_free_malaysia_shipping_preserves_total_and_rewards(self):
        country = self.env.ref('base.my')
        product = self.env['product.product'].create({'name': 'Free shipping fixture', 'type': 'service', 'list_price': 0, 'taxes_id': [(5, 0, 0)]})
        carrier = self.env['delivery.carrier'].create({'name': 'Malaysia free fixture', 'delivery_type': 'fixed', 'fixed_price': 0, 'product_id': product.id, 'country_ids': [(6, 0, country.ids)]})
        self.env['ir.config_parameter'].sudo().set_str('morimoto_mobile.carrier_id', str(carrier.id))
        for name in ['Pahang', 'Sabah', 'Sarawak']:
            state = self.env['res.country.state'].search([('country_id', '=', country.id), ('name', 'ilike', name)], limit=1)
            self.assertTrue(state)
            address = self.env['res.partner'].new({'name': name, 'country_id': country.id, 'state_id': state.id})
            self.assertEqual(Mobile()._free_delivery_carrier(self.env, address, self.env.company), carrier)
        order = self._create(3)
        before = order.amount_total
        gifts = order.order_line.filtered(lambda line: line.promotion_program_id == self.program).product_uom_qty
        order.set_delivery_line(carrier, 0)
        self.assertEqual(order.amount_total, before)
        self.assertEqual(len(order.order_line.filtered('is_delivery')), 1)
        self.assertEqual(order.order_line.filtered(lambda line: line.promotion_program_id == self.program).product_uom_qty, gifts)
        payload = quotation_payload(order)
        self.assertEqual([line['subtotal'] for line in payload['lines'] if line['is_delivery']], [0])
        carrier.fixed_price = 10
        with self.assertRaises(Conflict):
            Mobile()._free_delivery_carrier(self.env, address, self.env.company)
        carrier.fixed_price = 0
        address.country_id = self.env.ref('base.sg')
        with self.assertRaises(Conflict):
            Mobile()._free_delivery_carrier(self.env, address, self.env.company)
