import hashlib
import hmac
import logging
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
HOSTS = {
    "sandbox": "https://partner.test-stable.shopeemobile.com",
    "production": "https://partner.shopeemobile.com",
}


class MotoGeneShopeeShop(models.Model):
    _name = "motogene.shopee.shop"
    _description = "MotoGene Shopee Shop API Configuration"
    _rec_name = "name"

    name = fields.Char(required=True, default="MotoGene Shopee Test")
    active = fields.Boolean(default=True)
    environment = fields.Selection(
        [("sandbox", "Sandbox"), ("production", "Production")],
        default="sandbox", required=True,
    )
    partner_id = fields.Char(required=True, groups="base.group_system")
    partner_key = fields.Char(string="Partner Key", groups="base.group_system", copy=False)
    redirect_base_url = fields.Char(
        help="HTTPS Odoo origin only; for example https://my-staging.dev.odoo.com",
        groups="base.group_system",
    )
    shop_id = fields.Char(readonly=True, groups="base.group_system", copy=False)
    access_token = fields.Char(readonly=True, groups="base.group_system", copy=False)
    refresh_token = fields.Char(readonly=True, groups="base.group_system", copy=False)
    token_expiry = fields.Datetime(readonly=True, groups="base.group_system", copy=False)
    oauth_nonce_hash = fields.Char(readonly=True, groups="base.group_system", copy=False)
    oauth_nonce_expiry = fields.Datetime(readonly=True, groups="base.group_system", copy=False)
    state = fields.Selection(
        [("draft", "Not Connected"), ("connected", "Connected"),
         ("reauthorize", "Reauthorization Required")],
        default="draft", readonly=True, copy=False,
    )
    last_connection_test = fields.Datetime(readonly=True, copy=False)

    @api.constrains("redirect_base_url")
    def _check_redirect_base_url(self):
        from urllib.parse import urlsplit
        for rec in self:
            if not rec.redirect_base_url:
                continue
            parsed = urlsplit(rec.redirect_base_url)
            if (parsed.scheme != "https" or not parsed.hostname or
                    parsed.username or parsed.password or parsed.path not in ("", "/") or
                    parsed.query or parsed.fragment):
                raise UserError(_("Redirect base URL must be an HTTPS origin without a path or query."))

    def _host(self):
        self.ensure_one()
        return HOSTS[self.environment]

    def _partner_id_int(self):
        self.ensure_one()
        try:
            return int(self.partner_id)
        except (ValueError, TypeError):
            raise UserError(_("Partner ID must be numeric."))

    def _signature(self, path, timestamp, access_token=None, shop_id=None):
        self.ensure_one()
        if not self.partner_key:
            raise UserError(_("Configure Partner Key first."))
        raw = f"{self._partner_id_int()}{path}{timestamp}"
        if access_token is not None and shop_id is not None:
            raw += f"{access_token}{shop_id}"
        return hmac.new(
            self.partner_key.encode(), raw.encode(), hashlib.sha256
        ).hexdigest()

    def _public_post(self, path, payload):
        self.ensure_one()
        ts = int(time.time())
        params = {
            "partner_id": self._partner_id_int(),
            "timestamp": ts,
            "sign": self._signature(path, ts),
        }
        body = {"partner_id": self._partner_id_int(), **payload}
        try:
            res = requests.post(
                self._host() + path, params=params, json=body, timeout=20
            )
            res.raise_for_status()
            result = res.json()
        except (requests.RequestException, ValueError):
            _logger.warning("Shopee authentication HTTP/JSON failure for shop config %s", self.id)
            raise UserError(_("Unable to contact Shopee authentication endpoint."))
        if result.get("error"):
            _logger.warning("Shopee authentication rejected request for shop config %s: %s",
                            self.id, str(result.get("error"))[:80])
            raise UserError(_("Shopee authentication failed: %s") % str(result["error"])[:80])
        return result

    def action_connect(self):
        self.ensure_one()
        if self.environment != "sandbox":
            raise UserError(_("Production OAuth is deliberately disabled in Phase 1."))
        if not self.redirect_base_url or not self.partner_key:
            raise UserError(_("Configure Redirect Base URL and Partner Key first."))
        nonce = secrets.token_urlsafe(32)
        self.write({
            "oauth_nonce_hash": hashlib.sha256(nonce.encode()).hexdigest(),
            "oauth_nonce_expiry": fields.Datetime.now() + timedelta(minutes=10),
        })
        path = "/api/v2/shop/auth_partner"
        redirect = self.redirect_base_url.rstrip("/") + "/shopee/callback/" + nonce
        ts = int(time.time())
        params = {
            "partner_id": self._partner_id_int(),
            "timestamp": ts,
            "sign": self._signature(path, ts),
            "redirect": redirect,
        }
        return {"type": "ir.actions.act_url", "url": self._host() + path + "?" + urlencode(params),
                "target": "self"}

    def _exchange_code(self, code, shop_id):
        self.ensure_one()
        result = self._public_post(
            "/api/v2/auth/token/get",
            {"code": code, "shop_id": int(shop_id)},
        )
        if not result.get("access_token") or not result.get("refresh_token"):
            raise UserError(_("Shopee token response did not contain both tokens."))
        self.write({
            "shop_id": str(shop_id),
            "access_token": result["access_token"],
            "refresh_token": result["refresh_token"],
            "token_expiry": fields.Datetime.now() + timedelta(seconds=int(result.get("expire_in", 14400))),
            "oauth_nonce_hash": False,
            "oauth_nonce_expiry": False,
            "state": "connected",
        })

    def action_test_connection(self):
        self.ensure_one()
        if self.environment != "sandbox":
            raise UserError(_("Production API calls are disabled in Phase 1."))
        if not self.shop_id or not self.access_token:
            raise UserError(_("Authorize the Shopee shop first."))
        self._refresh_if_needed()
        path = "/api/v2/shop/get_shop_info"
        ts = int(time.time())
        params = {
            "partner_id": self._partner_id_int(),
            "timestamp": ts,
            "access_token": self.access_token,
            "shop_id": int(self.shop_id),
            "sign": self._signature(path, ts, self.access_token, self.shop_id),
        }
        try:
            r = requests.get(self._host() + path, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError):
            raise UserError(_("Shopee connection test failed: network or invalid JSON response."))
        if data.get("error"):
            raise UserError(_("Shopee connection test rejected: %s") % str(data["error"])[:80])
        self.last_connection_test = fields.Datetime.now()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("Shopee Connected"),
                       "message": _("Sandbox shop API responded successfully."),
                       "type": "success", "sticky": False},
        }

    def _refresh_if_needed(self):
        self.ensure_one()
        if not self.token_expiry or not self.refresh_token:
            return
        if self.token_expiry > fields.Datetime.now() + timedelta(minutes=20):
            return
        self.env.cr.execute(
            "SELECT id FROM motogene_shopee_shop WHERE id = %s FOR UPDATE", [self.id]
        )
        self.invalidate_recordset(["token_expiry", "refresh_token"])
        if self.token_expiry and self.token_expiry > fields.Datetime.now() + timedelta(minutes=20):
            return
        if self.environment != "sandbox":
            return
        try:
            result = self._public_post(
                "/api/v2/auth/access_token/get",
                {"refresh_token": self.refresh_token, "shop_id": int(self.shop_id)},
            )
            if not result.get("access_token") or not result.get("refresh_token"):
                raise UserError(_("Missing refreshed Shopee tokens."))
            self.write({
                "access_token": result["access_token"],
                "refresh_token": result["refresh_token"],
                "token_expiry": fields.Datetime.now() + timedelta(
                    seconds=int(result.get("expire_in", 14400))),
                "state": "connected",
            })
        except (UserError, ValueError):
            self.state = "reauthorize"
            _logger.warning("Shopee reauthorization may be required for shop config %s", self.id)

    @api.model
    def _cron_refresh_tokens(self):
        shops = self.sudo().search([
            ("active", "=", True),
            ("state", "=", "connected"),
            ("environment", "=", "sandbox"),
        ])
        for shop in shops:
            try:
                with self.env.cr.savepoint():
                    shop._refresh_if_needed()
            except Exception:
                _logger.exception("Shopee refresh task failed for shop config %s", shop.id)
