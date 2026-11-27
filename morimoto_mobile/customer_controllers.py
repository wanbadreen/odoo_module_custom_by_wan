import hashlib
import json
import secrets
from datetime import timedelta
from html import escape
from odoo import fields, http
from odoo.http import request
from werkzeug.exceptions import Forbidden, Unauthorized
from .customer_contract import email_address, phone_number, login_identifier, password_value, token_digest, delivery_values

HOST = 'percyianodoo-morimotoformulas-staging-37481016.dev.odoo.com'
SITE = 'https://morimoto-shopping-staging.badreengd.chatgpt.site'


def gateway():
    params = request.env['ir.config_parameter'].sudo()
    if (not request.httprequest.headers.get('Authorization', '').startswith('Bearer ')
            or request.httprequest.host.split(':')[0] != HOST
            or params.get_str('morimoto_mobile.database') != request.env.cr.dbname
            or params.get_str('morimoto_mobile.enabled') != 'staging'
            or params.get_str('morimoto_mobile.customer_auth') != 'staging'
            or not request.env.user.has_group('morimoto_mobile.group_mobile_api')):
        raise Forbidden()


def current_account():
    gateway()
    try:
        digest = token_digest(request.httprequest.headers.get('X-Morimoto-Customer-Session'))
    except ValueError:
        raise Unauthorized()
    session = request.env['morimoto.mobile.session'].sudo().search([
        ('token_hash', '=', digest), ('expires', '>', fields.Datetime.now()),
    ], limit=1)
    account = session.account_id
    if (not account or not account.active or not account.verified
            or session.version != account.version or not account.partner_id.active):
        raise Unauthorized()
    return account


class CustomerAuth(http.Controller):
    @http.route('/morimoto/customer/address', type='http', auth='bearer', methods=['GET', 'POST'], csrf=False)
    def address(self):
        account = current_account()
        country = request.env.ref('base.my')
        states = request.env['res.country.state'].sudo().search([('country_id', '=', country.id)], order='name')
        if request.httprequest.method == 'POST':
            try:
                data = delivery_values(self._data(['name', 'phone', 'street', 'city', 'zip', 'state_id'], ['street2']))
            except (ValueError, TypeError):
                return self._reply({'error': 'Semak alamat, telefon, negeri dan poskod lima digit.'}, 400)
            if data['state_id'] not in states.ids:
                return self._reply({'error': 'Negeri tidak sah.'}, 400)
            self._lock('address:%s' % account.id)
            account.invalidate_recordset(['shipping_partner_id'])
            old = account.shipping_partner_id
            same = old and old.active and old.parent_id == account.partner_id and all(
                (old[field].id if field == 'state_id' else old[field] or '') == value for field, value in data.items())
            if not same:
                # A new snapshot preserves delivery addresses on existing quotations.
                address = request.env['res.partner'].sudo().with_context(tracking_disable=True, mail_create_nosubscribe=True).create({
                    **data, 'type': 'delivery', 'parent_id': account.partner_id.id,
                    'country_id': country.id, 'company_id': account.partner_id.company_id.id,
                    'user_id': request.env.uid,
                })
                account.shipping_partner_id = address
        address = account.shipping_partner_id
        if address and (not address.active or address.parent_id != account.partner_id or address.country_id != country):
            raise Forbidden()
        return self._reply({'address': ({field: address[field] or '' for field in ['name', 'phone', 'street', 'street2', 'city', 'zip']} | {'state_id': str(address.state_id.id)}) if address else None,
                            'states': [{'id': str(state.id), 'name': state.name} for state in states]})

    def _reply(self, data, status=200):
        return request.make_json_response(data, status=status, headers=[('Cache-Control', 'no-store')])

    def _lock(self, key):
        lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
        request.env.cr.execute('SELECT pg_advisory_xact_lock(%s)', [lock])

    def _rate(self, name, limit, minutes):
        self._lock('rate:' + name)
        rates = request.env['morimoto.mobile.rate'].sudo()
        key = hashlib.sha256(name.encode()).hexdigest()
        rate = rates.search([('key', '=', key)], limit=1)
        now = fields.Datetime.now()
        if not rate:
            rate = rates.create({'key': key, 'count': 0, 'expires': now + timedelta(minutes=minutes)})
        if rate.expires <= now:
            rate.write({'count': 0, 'expires': now + timedelta(minutes=minutes)})
        rate.count += 1
        return rate.count <= limit

    def _data(self, required, optional=()):
        raw = request.httprequest.get_data(cache=True)
        if len(raw) > 4096:
            raise ValueError('Permintaan terlalu besar.')
        data = json.loads(raw)
        if not isinstance(data, dict) or not set(required) <= set(data) or set(data) - set(required) - set(optional):
            raise ValueError('Maklumat tidak sah.')
        if not all(isinstance(v, str) for v in data.values()):
            raise ValueError('Maklumat tidak sah.')
        return data

    def _mail(self, account, kind, sender):
        token = secrets.token_hex(32)
        account.write({'token_hash': token_digest(token), 'token_kind': kind,
                       'token_expires': fields.Datetime.now() + timedelta(minutes=30)})
        # Fragment keeps the one-time credential out of access logs and Referer headers.
        link = SITE + '/#account-token=' + token
        request.env['mail.mail'].sudo().create({
            'subject': 'Morimoto: sahkan email dan tetapkan kata laluan' if kind == 'verify' else 'Morimoto: reset kata laluan',
            'email_from': sender, 'email_to': account.email, 'auto_delete': True,
            'body_html': '<p>Buka pautan ini untuk menetapkan kata laluan. Pautan sah selama 30 minit.</p>'
                         '<p><a href="%s">Tetapkan kata laluan</a></p>' % escape(link, quote=True)
                         + '<p>Jika anda tidak membuat permintaan ini, abaikan email ini.</p>',
        })

    @http.route('/morimoto/customer/<string:action>', type='http', auth='bearer', methods=['GET', 'POST'], csrf=False)
    def customer(self, action):
        gateway()
        if action not in ('register', 'recover', 'verify', 'login', 'session', 'logout'):
            return self._reply({'error': 'Tidak ditemui.'}, 404)
        if request.httprequest.method != ('GET' if action == 'session' else 'POST'):
            return self._reply({'error': 'Kaedah tidak dibenarkan.'}, 405)
        if action == 'session':
            account = current_account()
            return self._reply({'customer': {'name': account.name, 'email': account.email}})
        if action == 'logout':
            try:
                digest = token_digest(request.httprequest.headers.get('X-Morimoto-Customer-Session'))
                request.env['morimoto.mobile.session'].sudo().search([('token_hash', '=', digest)]).unlink()
            except ValueError:
                pass
            return self._reply({'ok': True})
        if not self._rate('auth-global', 100, 1):
            return self._reply({'error': 'Terlalu banyak percubaan. Cuba sebentar lagi.'}, 429)
        accounts = request.env['morimoto.mobile.account'].sudo().with_context(active_test=False)
        try:
            if action in ('register', 'recover'):
                data = self._data(['email', 'name'] if action == 'register' else ['email'], ['phone'] if action == 'register' else [])
                email = email_address(data['email'])
                sender = request.env['ir.config_parameter'].sudo().get_str('morimoto_mobile.auth_email_from')
                if not sender:
                    return self._reply({'error': 'Pengesahan email belum tersedia. Hubungi Morimoto.'}, 503)
                email_address(sender)
                if not self._rate('mail:' + email, 3, 30):
                    return self._reply({'message': 'Jika email ini boleh digunakan, arahan akan dihantar. Semak inbox dan spam.'}, 202)
                self._lock('account:' + email)
                account = accounts.search([('email', '=', email)], limit=1)
                if action == 'register' and not account:
                    name = data['name'].strip()
                    if not 1 <= len(name) <= 100:
                        raise ValueError('Isi nama anda, maksimum 100 aksara.')
                    phone = phone_number(data['phone']) if data.get('phone', '').strip() else False
                    account = accounts.create({'name': name, 'email': email, 'pending_phone': phone})
                if account and account.active and (action == 'recover' or not account.verified):
                    self._mail(account, 'reset' if account.verified else 'verify', sender)
                return self._reply({'message': 'Jika email ini boleh digunakan, arahan akan dihantar. Semak inbox dan spam.'}, 202)
            if action == 'verify':
                data = self._data(['token', 'password'])
                digest = token_digest(data['token'])
                password = password_value(data['password'])
                self._lock('verify:' + digest)
                account = accounts.search([('token_hash', '=', digest), ('active', '=', True),
                                           ('token_expires', '>', fields.Datetime.now())], limit=1)
                if not account:
                    return self._reply({'error': 'Pautan tidak sah atau sudah tamat tempoh. Minta pautan baharu.'}, 400)
                company_id = int(request.env['ir.config_parameter'].sudo().get_str('morimoto_mobile.company_id', '0'))
                if company_id not in request.env.user.company_ids.ids:
                    raise Forbidden()
                values = {'password_hash': request.env['res.users']._crypt_context().hash(password),
                          'verified': True, 'version': account.version + 1,
                          'token_hash': False, 'token_kind': False, 'token_expires': False}
                if not account.partner_id:
                    # Never claim an existing Odoo contact based solely on matching identifiers.
                    partner = request.env['res.partner'].sudo().create({
                        'name': account.name, 'email': account.email, 'phone': account.pending_phone,
                        'company_id': company_id, 'user_id': request.env.uid, 'customer_rank': 1,
                    })
                    values['partner_id'] = partner.id
                if account.pending_phone:
                    self._lock('phone:' + account.pending_phone)
                    if not accounts.search_count([('phone', '=', account.pending_phone), ('id', '!=', account.id)]):
                        values['phone'] = account.pending_phone
                    values['pending_phone'] = False
                account.write(values)
                return self._reply({'message': 'Kata laluan disimpan. Anda boleh log masuk menggunakan email.', 'verified': True})
            data = self._data(['identifier', 'password'])
            field, identifier = login_identifier(data['identifier'])
            if not self._rate('login:' + identifier, 10, 15):
                return self._reply({'error': 'Terlalu banyak percubaan. Cuba lagi selepas 15 minit.'}, 429)
            self._lock('account-login:' + identifier)
            account = accounts.search([(field, '=', identifier), ('active', '=', True), ('verified', '=', True)], limit=1)
            password = password_value(data['password'])
            ctx = request.env['res.users']._crypt_context()
            valid = bool(account and account.password_hash and ctx.verify(password, account.password_hash))
            if not account:
                ctx.hash(password)  # Comparable password work for unknown identifiers.
            if not valid or not account.partner_id.active:
                return self._reply({'error': 'Maklumat login tidak sah atau akaun belum disahkan.'}, 401)
            token = secrets.token_hex(32)
            request.env['morimoto.mobile.session'].sudo().create({
                'account_id': account.id, 'token_hash': token_digest(token), 'version': account.version,
                'expires': fields.Datetime.now() + timedelta(hours=2),
            })
            # Gateway consumes this value and puts it in an HttpOnly cookie, never browser JSON.
            return self._reply({'session_token': token, 'customer': {'name': account.name, 'email': account.email}})
        except (ValueError, TypeError):
            return self._reply({'error': 'Semak maklumat anda. Kata laluan mesti 12 hingga 128 aksara; nombor telefon perlu kod negara.'}, 400)
