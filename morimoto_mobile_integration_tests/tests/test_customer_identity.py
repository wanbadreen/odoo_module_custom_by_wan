from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from odoo import fields
from odoo.tests.common import TransactionCase, tagged, new_test_user
from werkzeug.exceptions import Unauthorized
from odoo.addons.morimoto_mobile.customer_contract import token_digest
from odoo.addons.morimoto_mobile.customer_controllers import current_account, HOST, CustomerAuth
from odoo.addons.morimoto_mobile.controllers import Mobile


@tagged('post_install', '-at_install', 'morimoto_mobile_customer')
class TestCustomerIdentity(TransactionCase):
    def setUp(self):
        super().setUp()
        self.service = new_test_user(self.env, login='mobile-auth-fixture', groups='base.group_user,sales_team.group_sale_salesman,morimoto_mobile.group_mobile_api')
        self.partners = self.env['res.partner'].create([
            {'name': 'Customer A', 'user_id': self.service.id},
            {'name': 'Customer B', 'user_id': self.service.id},
        ])
        self.accounts = self.env['morimoto.mobile.account'].create([
            {'name': p.name, 'email': 'customer%s@example.invalid' % p.id, 'verified': True, 'partner_id': p.id}
            for p in self.partners
        ])
        self.token = 'a' * 64
        self.session = self.env['morimoto.mobile.session'].create({
            'account_id': self.accounts[0].id, 'token_hash': token_digest(self.token),
            'version': self.accounts[0].version, 'expires': fields.Datetime.now() + timedelta(hours=1),
        })
        params = self.env['ir.config_parameter'].sudo()
        for key, value in {'enabled': 'staging', 'customer_auth': 'staging', 'database': self.env.cr.dbname,
                           'company_id': str(self.env.company.id), 'test_partner_id': str(self.partners[1].id)}.items():
            params.set_str('morimoto_mobile.' + key, value)
        self.fake = SimpleNamespace(env=self.env(user=self.service.id))
        self.fake.httprequest = SimpleNamespace(host=HOST, headers={
            'Authorization': 'Bearer fixture', 'X-Morimoto-Customer-Session': self.token,
        })
        self.patch1 = patch('odoo.addons.morimoto_mobile.customer_controllers.request', self.fake)
        self.patch2 = patch('odoo.addons.morimoto_mobile.controllers.request', self.fake)
        self.patch1.start()
        self.patch2.start()
        self.addCleanup(self.patch1.stop)
        self.addCleanup(self.patch2.stop)

    def test_session_resolves_only_its_own_customer(self):
        self.assertEqual(current_account(), self.accounts[0])
        _env, partner, _company = Mobile()._context()
        self.assertEqual(partner.id, self.partners[0].id)
        self.assertNotEqual(partner.id, self.partners[1].id)

    def test_address_is_owned_and_changes_preserve_old_snapshot(self):
        import json
        country = self.env.ref('base.my')
        state = self.env['res.country.state'].search([('country_id', '=', country.id)], limit=1)
        self.assertTrue(state)
        data = {'name': 'Receiver A', 'phone': '+60123456789', 'street': '1 Jalan Ujian', 'street2': '', 'city': 'Test City', 'zip': '50000', 'state_id': str(state.id)}
        self.fake.make_json_response = lambda data, **kwargs: data
        self.fake.httprequest.method = 'POST'
        self.fake.httprequest.get_data = lambda **kwargs: json.dumps(data).encode()
        CustomerAuth().address()
        first = self.accounts[0].shipping_partner_id
        self.assertEqual(first.parent_id, self.partners[0])
        self.assertFalse(self.accounts[1].shipping_partner_id)
        CustomerAuth().address()
        self.assertEqual(self.accounts[0].shipping_partner_id, first)
        data['street'] = '2 Jalan Baru'
        CustomerAuth().address()
        self.assertNotEqual(self.accounts[0].shipping_partner_id, first)
        self.assertEqual(first.street, '1 Jalan Ujian')
        data['partner_id'] = str(self.partners[1].id)
        reply = CustomerAuth().address()
        self.assertIn('error', reply)
        self.assertFalse(self.accounts[1].shipping_partner_id)

    def test_revoked_and_expired_sessions_are_rejected(self):
        self.accounts[0].version += 1
        with self.assertRaises(Unauthorized):
            current_account()
        self.session.version = self.accounts[0].version
        self.session.expires = fields.Datetime.now() - timedelta(seconds=1)
        with self.assertRaises(Unauthorized):
            current_account()

    def test_disabled_accounts_and_unknown_tokens_are_rejected(self):
        self.accounts[0].active = False
        with self.assertRaises(Unauthorized):
            current_account()
        self.fake.httprequest.headers['X-Morimoto-Customer-Session'] = 'b' * 64
        with self.assertRaises(Unauthorized):
            current_account()
