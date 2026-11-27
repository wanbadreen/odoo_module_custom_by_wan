import base64
import hashlib
import json
from odoo import http
from odoo.http import request
from werkzeug.exceptions import BadRequest, Forbidden, Conflict
from .contract import validate_cart, quotation_payload
from .customer_contract import customer_request_key
from .customer_controllers import current_account

STAGING_HOST = 'percyianodoo-morimotoformulas-staging-37481016.dev.odoo.com'

class Mobile(http.Controller):
    def _free_delivery_carrier(self, env, shipping, company):
        # One existing carrier, selected server-side. Never accept client prices or IDs.
        carrier_id = int(env['ir.config_parameter'].sudo().get_str('morimoto_mobile.carrier_id', '2'))
        carrier = env['delivery.carrier'].browse(carrier_id).exists()
        country = env.ref('base.my')
        if (shipping.country_id != country or not carrier or not carrier.active
                or carrier.delivery_type != 'fixed' or carrier.fixed_price != 0
                or (carrier.company_id and carrier.company_id != company)
                or (carrier.country_ids and country not in carrier.country_ids)
                or carrier.state_ids or carrier.zip_prefix_ids or not carrier.product_id.active):
            raise Conflict('Free Malaysia delivery configuration is unavailable')
        return carrier

    def _context(self):
        # Mandatory explicit bearer header; do not accept session-cookie fallback.
        if not request.httprequest.headers.get('Authorization', '').startswith('Bearer '):
            raise Forbidden()
        params = request.env['ir.config_parameter'].sudo()
        if (params.get_str('morimoto_mobile.enabled') != 'staging'
                or request.httprequest.host.split(':')[0] != STAGING_HOST
                or params.get_str('morimoto_mobile.database') != request.env.cr.dbname
                or not request.env.user.has_group('morimoto_mobile.group_mobile_api')):
            raise Forbidden()
        try:
            partner_id = int(params.get_str('morimoto_mobile.test_partner_id', '0'))
            company_id = int(params.get_str('morimoto_mobile.company_id', '0'))
        except ValueError:
            raise Forbidden()
        company = request.env['res.company'].browse(company_id).exists()
        if not company or company not in request.env.user.company_ids:
            raise Forbidden()
        env = request.env(context=dict(request.env.context, allowed_company_ids=[company.id]))
        if request.httprequest.headers.get('X-Morimoto-Customer-Session'):
            partner_id = current_account().partner_id.id
        partner = env['res.partner'].browse(partner_id).exists()
        if not partner or not partner.active or (partner.company_id and partner.company_id != company):
            raise Forbidden()
        pricelist = partner.property_product_pricelist
        if not pricelist or not pricelist.active or (pricelist.company_id and pricelist.company_id != company):
            raise Forbidden()
        return env, partner, company

    def _domain(self, company):
        return [('morimoto_mobile_enabled', '=', True), ('sale_ok', '=', True), ('active', '=', True), ('company_id', 'in', [False, company.id])]

    @http.route('/morimoto/mobile/products', type='http', auth='bearer', methods=['GET'], csrf=False)
    def products(self):
        env, partner, company = self._context()
        pricelist = partner.property_product_pricelist
        products = env['product.product'].search(self._domain(company), limit=100, order='id')
        def catalog_item(product):
            image = product.image_128
            image_data = None
            # Odoo 20 binary fields return BinaryValue (raw bytes), not base64.
            if image:
                raw = image.content
                if len(raw) <= 262144:
                    mime = ('image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else
                            'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else None)
                    if mime:
                        encoded = base64.b64encode(raw).decode('ascii')
                        image_data = 'data:' + mime + ';base64,' + encoded
            return {
                'id': product.id, 'name': product.display_name,
                'description': (product.description_sale or '')[:4000],
                'image_data': image_data,
                'price': pricelist._get_product_price(product, 1.0),
                'currency': pricelist.currency_id.name,
            }
        return request.make_json_response({'products': [catalog_item(p) for p in products]}, headers=[('Cache-Control', 'no-store')])

    @http.route('/morimoto/mobile/quotations', type='http', auth='bearer', methods=['POST'], csrf=False)
    def quotation(self):
        env, partner, company = self._context()
        shipping = None
        if request.httprequest.headers.get('X-Morimoto-Customer-Session'):
            shipping = current_account().shipping_partner_id
            if not shipping or not shipping.active or shipping.parent_id != partner or (shipping.company_id and shipping.company_id != company):
                return request.make_json_response({'error': 'Simpan alamat penghantaran dahulu.'}, status=400)
        raw = request.httprequest.get_data(cache=True)
        if len(raw) > 16384:
            raise BadRequest()
        key = request.httprequest.headers.get('Idempotency-Key', '')
        try:
            lines = validate_cart(json.loads(raw), key)
            seen = {line['product_id'] for line in lines}
        except (ValueError, TypeError):
            raise BadRequest()
        if request.httprequest.headers.get('X-Morimoto-Customer-Session'):
            key = customer_request_key(current_account().id, key)
        fingerprint_data = {'lines': lines, 'shipping_id': shipping.id} if shipping else sorted(lines, key=lambda l: l['product_id'])
        fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
        # Serialise same-key requests across workers; SQL uniqueness is the final guard.
        lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
        env.cr.execute('SELECT pg_advisory_xact_lock(%s)', [lock])
        order = env['sale.order'].search([('morimoto_mobile_key', '=', key)], limit=1)
        if order:
            if order.partner_id != partner or order.company_id != company or order.morimoto_mobile_fingerprint != fingerprint:
                raise Conflict()
        else:
            products = env['product.product'].search(self._domain(company) + [('id', 'in', list(seen))])
            if set(products.ids) != seen:
                raise BadRequest()
            # No sudo: dedicated service user must have ordinary sale/product ACLs.
            order = env['sale.order'].with_context(tracking_disable=True, mail_create_nosubscribe=True).create({
                'partner_id': partner.id, 'company_id': company.id,
                **({'partner_shipping_id': shipping.id, 'partner_invoice_id': partner.id} if shipping else {}),
                'pricelist_id': partner.property_product_pricelist.id,
                'morimoto_mobile_key': key, 'morimoto_mobile_fingerprint': fingerprint,
                'client_order_ref': 'MORIMOTO-STAGING-' + key,
                'order_line': [(0, 0, {'product_id': l['product_id'], 'product_uom_qty': l['quantity']}) for l in lines],
            })
            if order.state != 'draft':
                raise Conflict('Custom module changed quotation state; transaction rolled back')
            if shipping:
                carrier = self._free_delivery_carrier(env, shipping, company)
                order.set_delivery_line(carrier, 0.0)
                delivery = order.order_line.filtered('is_delivery')
                if len(delivery) != 1 or not order.currency_id.is_zero(delivery.price_total) or order.state != 'draft':
                    raise Conflict('Free delivery could not be applied; transaction rolled back')
        # Existing promotion create hooks have already run. Return all generated
        # rewards, even when the gift itself is not in the mobile catalog.
        return request.make_json_response(quotation_payload(order), headers=[('Cache-Control', 'no-store')])
