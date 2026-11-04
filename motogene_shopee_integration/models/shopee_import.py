import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ShopeeProductMapping(models.Model):
    _name = "motogene.shopee.product.mapping"
    _description = "Shopee Item/Variation to Odoo Product Mapping"
    _rec_name = "product_id"

    shop_id = fields.Many2one("motogene.shopee.shop", required=True, ondelete="cascade")
    shopee_item_id = fields.Char(required=True, index=True)
    shopee_model_id = fields.Char(default="0", required=True, index=True)
    seller_sku = fields.Char()
    product_id = fields.Many2one("product.product", required=True, ondelete="restrict")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("mapping_shop_item_model_unique",
         "unique(shop_id, shopee_item_id, shopee_model_id)",
         "This Shopee shop/item/model mapping already exists."),
    ]


class ShopeeImportedOrder(models.Model):
    _name = "motogene.shopee.import.order"
    _description = "Shopee Order Read-Only Preview"
    _order = "shopee_create_time desc, id desc"

    name = fields.Char(required=True, index=True, readonly=True)
    shop_id = fields.Many2one("motogene.shopee.shop", required=True, ondelete="cascade", readonly=True)
    order_status = fields.Char(readonly=True)
    shopee_create_time = fields.Datetime(readonly=True)
    currency = fields.Char(readonly=True)
    buyer_total = fields.Float(readonly=True, digits=(16, 2))
    mapping_status = fields.Selection(
        [("matched", "Matched"), ("review", "Review Required"), ("empty", "No Item Data")],
        default="empty", readonly=True,
    )
    line_ids = fields.One2many("motogene.shopee.import.line", "order_id", readonly=True)
    last_sync_at = fields.Datetime(readonly=True)
    _sql_constraints = [
        ("shopee_order_per_shop_unique", "unique(shop_id, name)",
         "Shopee order already exists for this shop."),
    ]


class ShopeeImportedLine(models.Model):
    _name = "motogene.shopee.import.line"
    _description = "Shopee Read-Only Order Line"

    order_id = fields.Many2one(
        "motogene.shopee.import.order", required=True, ondelete="cascade", readonly=True)
    item_id = fields.Char(readonly=True)
    model_id = fields.Char(readonly=True)
    item_name = fields.Char(readonly=True)
    seller_sku = fields.Char(readonly=True)
    quantity = fields.Float(readonly=True)
    shopee_original_price = fields.Float(readonly=True, digits=(16, 2))
    shopee_discounted_price = fields.Float(readonly=True, digits=(16, 2))
    product_id = fields.Many2one("product.product", readonly=True)
    matched = fields.Boolean(readonly=True)


class MotoGeneShopeeShop(models.Model):
    _inherit = "motogene.shopee.shop"

    def _read_only_api_get(self, path, payload):
        self.ensure_one()
        if self.environment != "sandbox":
            raise UserError(_("Only sandbox read-only import is supported in Phase 2."))
        if self.state != "connected" or not self.shop_id:
            raise UserError(_("Connect the sandbox shop before importing."))
        self._refresh_if_needed()
        if self.state != "connected":
            raise UserError(_("Shop requires reauthorization."))
        ts = int(time.time())
        params = {
            "partner_id": self._partner_id_int(),
            "timestamp": ts,
            "access_token": self.access_token,
            "shop_id": int(self.shop_id),
            "sign": self._signature(path, ts, self.access_token, self.shop_id),
            **payload,
        }
        try:
            resp = requests.get(self._host() + path, params=params, timeout=25)
            resp.raise_for_status()
            result = resp.json()
        except (requests.RequestException, ValueError):
            _logger.warning("Shopee order preview request failed for config %s", self.id)
            raise UserError(_("Shopee read-only API request failed."))
        if result.get("error"):
            raise UserError(_("Shopee API error: %s") % str(result["error"])[:100])
        if not isinstance(result.get("response"), dict):
            raise UserError(_("Unexpected Shopee API response."))
        return result["response"]

    def action_preview_recent_orders(self):
        self.ensure_one()
        if self.environment != "sandbox":
            raise UserError(_("Read-only order preview is limited to sandbox."))
        # Safe bounded lookback: 3 days, up to 150 orders per button click.
        now = int(time.time())
        begin = now - 3 * 86400
        cursor = ""
        sn_list = []
        for _page in range(3):
            data = self._read_only_api_get("/api/v2/order/get_order_list", {
                "time_range_field": "create_time",
                "time_from": begin,
                "time_to": now,
                "page_size": 50,
                "cursor": cursor,
            })
            sn_list.extend(
                str(v.get("order_sn")) for v in data.get("order_list", [])
                if v.get("order_sn")
            )
            if not data.get("more"):
                break
            new_cursor = data.get("next_cursor", "")
            if not new_cursor or new_cursor == cursor:
                break
            cursor = new_cursor
        imported = 0
        for start in range(0, len(set(sn_list)), 50):
            ids = list(dict.fromkeys(sn_list))[start:start + 50]
            details = self._read_only_api_get("/api/v2/order/get_order_detail", {
                "order_sn_list": ",".join(ids),
                "response_optional_fields": "total_amount,currency",
            })
            for detail in details.get("order_list", []):
                if not detail.get("order_sn"):
                    continue
                self._upsert_order_preview(detail)
                imported += 1
        return {
            "type": "ir.actions.act_window",
            "name": _("Shopee Order Previews (%s)") % imported,
            "res_model": "motogene.shopee.import.order",
            "view_mode": "list,form",
            "domain": [("shop_id", "=", self.id)],
        }

    def _upsert_order_preview(self, detail):
        self.ensure_one()
        Order = self.env["motogene.shopee.import.order"].sudo()
        Mapping = self.env["motogene.shopee.product.mapping"].sudo()
        sn = str(detail["order_sn"])
        order = Order.search([("shop_id", "=", self.id), ("name", "=", sn)], limit=1)
        raw_create = detail.get("create_time")
        create_dt = (datetime.fromtimestamp(int(raw_create), tz=timezone.utc)
                     .replace(tzinfo=None) if raw_create else False)
        lines = []
        for item in detail.get("item_list") or []:
            item_id = str(item.get("item_id") or "")
            model_id = str(item.get("model_id") or "0")
            mapping = Mapping.search([
                ("shop_id", "=", self.id), ("shopee_item_id", "=", item_id),
                ("shopee_model_id", "=", model_id), ("active", "=", True)
            ], limit=1)
            lines.append((0, 0, {
                "item_id": item_id,
                "model_id": model_id,
                "item_name": str(item.get("item_name") or "")[:255],
                "seller_sku": str(item.get("model_sku") or item.get("item_sku") or "")[:255],
                "quantity": float(item.get("model_quantity_purchased") or 0),
                "shopee_original_price": float(item.get("model_original_price") or 0),
                "shopee_discounted_price": float(item.get("model_discounted_price") or 0),
                "product_id": mapping.product_id.id if mapping else False,
                "matched": bool(mapping),
            }))
        status = ("empty" if not lines else
                  "matched" if all(v[2]["matched"] for v in lines) else "review")
        vals = {
            "name": sn,
            "shop_id": self.id,
            "order_status": str(detail.get("order_status") or ""),
            "shopee_create_time": create_dt,
            "currency": str(detail.get("currency") or ""),
            "buyer_total": float(detail.get("total_amount") or 0),
            "mapping_status": status,
            "last_sync_at": fields.Datetime.now(),
        }
        if order:
            # Replace preview items atomically; no sale.order or stock interaction.
            order.write({**vals, "line_ids": [(5, 0, 0)] + lines})
        else:
            Order.create({**vals, "line_ids": lines})
