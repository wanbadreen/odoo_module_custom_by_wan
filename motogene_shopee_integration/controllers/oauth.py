import hashlib
import hmac
import logging
from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


class MotoGeneShopeeCallback(http.Controller):

    @http.route("/shopee/callback/<string:nonce>", type="http",
                auth="public", methods=["GET"], csrf=False, sitemap=False)
    def shopee_callback(self, nonce, **kwargs):
        # Never print callback query parameters: they contain a short-lived auth code.
        if len(nonce) < 30 or len(nonce) > 128:
            return request.make_response("Invalid authorization state.", status=400)
        nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
        shops = request.env["motogene.shopee.shop"].sudo().search([
            ("oauth_nonce_expiry", ">", fields.Datetime.now()),
            ("environment", "in", ["sandbox", "production"]),
        ])
        shop = next((s for s in shops if s.oauth_nonce_hash and
                     hmac.compare_digest(s.oauth_nonce_hash, nonce_hash)), None)
        if not shop:
            return request.make_response("Authorization expired or invalid.", status=400)
        try:
            shop._check_environment_access()
        except Exception:
            return request.make_response("Shopee environment is not enabled.", status=403)
        code = kwargs.get("code")
        shop_id = kwargs.get("shop_id")
        if not code or not shop_id:
            return request.make_response("Shopee did not provide code and shop ID.", status=400)
        try:
            shop._exchange_code(code, shop_id)
        except Exception:
            _logger.exception("Shopee authorization exchange failed for config %s", shop.id)
            return request.make_response("Shopee authorization failed. Check Odoo logs.", status=400)
        return request.redirect("/web#action=motogene_shopee_integration.action_shopee_shop")
