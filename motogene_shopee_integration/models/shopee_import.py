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
    shopee_main_product = fields.Char(string="Shopee Main Product", readonly=True, copy=False)
    shopee_variation_name = fields.Char(string="Shopee Variation", readonly=True, copy=False)
    quantity_multiplier = fields.Integer(
        string="Quantity Multiplier", required=True, default=1,
        help="Odoo units per Shopee unit; for example one 2OS30 set equals two OS30 boxes.")
    product_id = fields.Many2one("product.product", required=True, ondelete="restrict")

    @api.constrains("quantity_multiplier")
    def _check_quantity_multiplier(self):
        for rec in self:
            if rec.quantity_multiplier < 1:
                raise UserError(_("Quantity Multiplier must be a positive whole number."))

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
    buyer_username = fields.Char(
        string="Shopee Buyer Username", readonly=True, copy=False,
        groups="base.group_system",
    )
    buyer_user_id = fields.Char(string="Shopee Buyer User ID", readonly=True, copy=False,
                                groups="base.group_system")
    recipient_name = fields.Char(readonly=True, copy=False, groups="base.group_system")
    recipient_phone = fields.Char(readonly=True, copy=False, groups="base.group_system")
    recipient_full_address = fields.Char(readonly=True, copy=False, groups="base.group_system")
    recipient_city = fields.Char(readonly=True, copy=False, groups="base.group_system")
    recipient_state = fields.Char(readonly=True, copy=False, groups="base.group_system")
    recipient_zipcode = fields.Char(readonly=True, copy=False, groups="base.group_system")
    recipient_region = fields.Char(readonly=True, copy=False, groups="base.group_system")
    recipient_ready = fields.Boolean(readonly=True, copy=False, groups="base.group_system")

    escrow_amount = fields.Float(
        string="Shopee Estimated Net Income", readonly=True, digits=(16, 2))
    escrow_status = fields.Selection([
        ("not_checked", "Not Checked"),
        ("available", "Available"),
        ("unavailable", "Not Available"),
    ], default="not_checked", readonly=True)
    escrow_last_sync = fields.Datetime(readonly=True)

    related_sale_order_id = fields.Many2one(
        "sale.order", string="Related Sales Order",
        compute="_compute_related_sale_order", compute_sudo=True, readonly=True,
    )

    def _compute_related_sale_order(self):
        Sale = self.env["sale.order"].sudo()
        for record in self:
            record.related_sale_order_id = Sale.search([
                ("motogene_shopee_preview_id", "=", record.id)
            ], limit=1)

    @api.model
    def motogene_shopee_pending_order_count(self):
        """Badge counts only imported, READY_TO_SHIP orders with no linked SO."""
        if not self.env.user.has_group("base.group_system"):
            return 0
        previews = self.sudo().search([
            ("shop_id.environment", "in", ["sandbox", "production"]),
            ("shop_id.active", "=", True),
            ("order_status", "=", "READY_TO_SHIP"),
        ])
        if not previews:
            return 0
        linked = set(self.env["sale.order"].sudo().search([
            "|",
            ("motogene_shopee_preview_id", "in", previews.ids),
            ("motogene_shopee_order_ref", "in", previews.mapped("name")),
        ]).mapped("motogene_shopee_order_ref"))
        linked_previews = set(self.env["sale.order"].sudo().search([
            ("motogene_shopee_preview_id", "in", previews.ids)
        ]).mapped("motogene_shopee_preview_id").ids)
        return sum(
            1 for row in previews
            if row.id not in linked_previews and row.name not in linked
        )

    def action_view_related_so(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only administrators can view linked test Sales Orders."))
        so = self.related_sale_order_id
        if not so:
            raise UserError(_("No Sales Order is linked to this Shopee Order Preview."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Related Sales Order"),
            "res_model": "sale.order",
            "res_id": so.id,
            "view_mode": "form",
            "target": "current",
        }


    def action_sync_escrow(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only administrators can sync sandbox financial data."))
        self.shop_id._check_environment_access()
        response = self.shop_id.sudo()._read_only_api_get(
            "/api/v2/payment/get_escrow_detail", {"order_sn": self.name}
        )
        income = response.get("order_income") or {}
        value = income.get("escrow_amount")
        if value is None:
            self.sudo().write({
                "escrow_status": "unavailable",
                "escrow_last_sync": fields.Datetime.now(),
            })
        else:
            self.sudo().write({
                "escrow_amount": float(value),
                "escrow_status": "available",
                "escrow_last_sync": fields.Datetime.now(),
            })
        updated = False
        if value is not None:
            Sale = self.env["sale.order"].sudo()
            so = Sale.search([("motogene_shopee_preview_id", "=", self.id)], limit=1)
            if so and so.state == "draft":
                vals = {}
                selection = Sale.fields_get(
                    ["x_studio_order_type"], attributes=["selection"]
                ).get("x_studio_order_type", {}) if "x_studio_order_type" in Sale._fields else {}
                if "SHOPEE " in dict(selection.get("selection") or []):
                    vals["x_studio_order_type"] = "SHOPEE "
                if ("x_studio_estimated_income_shopee_order_only" in Sale._fields
                        and (not self.currency or self.currency == so.currency_id.name)):
                    vals["x_studio_estimated_income_shopee_order_only"] = float(value)
                    updated = True
                if vals:
                    so.write(vals)
        msg = (_("Linked draft quotation estimated income updated.")
               if updated else
               _("Escrow preview saved; no Sales Order financial field was updated."))
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"title": _("Escrow Preview Synced"),
                           "message": msg,
                           "type": "success", "sticky": False}}

    mapping_status = fields.Selection(
        [("matched", "Matched"), ("review", "Review Required"), ("empty", "No Item Data")],
        default="empty", readonly=True,
    )
    line_ids = fields.One2many("motogene.shopee.import.line", "order_id", readonly=True)
    last_sync_at = fields.Datetime(readonly=True)
    def action_open_create_sale_wizard(self):
        self.ensure_one()
        if self.mapping_status != "matched" or self.order_status != "READY_TO_SHIP":
            raise UserError(_("The Shopee order is not ready for a draft quotation."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "motogene.shopee.create.sale.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_preview_id": self.id},
        }

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
    quantity_multiplier = fields.Integer(readonly=True, default=1)


class MotoGeneShopeeShop(models.Model):
    _inherit = "motogene.shopee.shop"

    def _read_only_api_get(self, path, payload):
        self.ensure_one()
        self._check_environment_access()
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

    @api.model
    def _cron_motogene_shopee_auto_sync(self):
        """Opt-in sandbox order discovery, read-only from Shopee; no SO creation."""
        conf = self.env["ir.config_parameter"].sudo()
        if conf.get_param("motogene_shopee.enable_auto_order_sync", "0") != "1":
            return
        shops = self.sudo().search([
            ("environment", "in", ["sandbox", "production"]), ("state", "=", "connected"),
            ("active", "=", True),
        ], limit=5)
        for shop in shops:
            if shop.environment == "production" and (
                conf.get_param("motogene_shopee.enable_production_auto_sync", "0") != "1"
            ):
                continue
            try:
                with self.env.cr.savepoint():
                    # Prevent overlapping cron executions for the same shop.
                    self.env.cr.execute(
                        "SELECT pg_try_advisory_xact_lock(%s, %s)",
                        [741231, shop.id],
                    )
                    if not self.env.cr.fetchone()[0]:
                        continue
                    result = shop._motogene_shopee_sync_order_previews()
                    _logger.info(
                        "Shopee auto sync shop=%s retrieved=%s new=%s",
                        shop.id, result["retrieved"], result["new"],
                    )
            except Exception:
                _logger.exception("Shopee auto order sync failed for shop %s", shop.id)

    def _motogene_shopee_sync_order_previews(self):
        """Bounded 3-day lookback; upsert by (shop, order SN), safe to repeat."""
        self.ensure_one()
        self._check_environment_access()
        if self.state != "connected":
            raise UserError(_("Connected Shopee Shop required."))
        now = int(time.time())
        begin = now - 3 * 86400
        cursor = ""
        numbers = []
        for _page in range(3):
            data = self._read_only_api_get("/api/v2/order/get_order_list", {
                "time_range_field": "create_time",
                "time_from": begin,
                "time_to": now,
                "page_size": 50,
                "cursor": cursor,
            })
            numbers.extend(
                str(row["order_sn"]) for row in data.get("order_list", [])
                if row.get("order_sn")
            )
            if not data.get("more"):
                break
            new_cursor = data.get("next_cursor") or ""
            if not new_cursor or new_cursor == cursor:
                break
            cursor = new_cursor
        numbers = list(dict.fromkeys(numbers))
        Preview = self.env["motogene.shopee.import.order"].sudo()
        existing = set(Preview.search([
            ("shop_id", "=", self.id), ("name", "in", numbers)
        ]).mapped("name"))
        retrieved = 0
        for start in range(0, len(numbers), 50):
            details = self._read_only_api_get("/api/v2/order/get_order_detail", {
                "order_sn_list": ",".join(numbers[start:start + 50]),
                "response_optional_fields": "item_list,total_amount,buyer_username,buyer_user_id,recipient_address",
            })
            for detail in details.get("order_list", []):
                if detail.get("order_sn"):
                    self._upsert_order_preview(detail)
                    retrieved += 1
        return {"retrieved": retrieved, "new": len(set(numbers) - existing)}

    def action_preview_recent_orders(self):
        self.ensure_one()
        self._check_environment_access()
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
                "response_optional_fields": "item_list,total_amount,buyer_username,buyer_user_id,recipient_address",
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
                "quantity_multiplier": mapping.quantity_multiplier if mapping else 1,
            }))
        status = ("empty" if not lines else
                  "matched" if all(v[2]["matched"] for v in lines) else "review")
        recipient = detail.get("recipient_address") or {}
        def clean(key, length=255):
            value = str(recipient.get(key) or "").strip()
            return value[:length] if value and "*" not in value else ""
        recipient_name = clean("name")
        recipient_address = clean("full_address", 1024)
        recipient_zip = clean("zipcode", 32)
        recipient_region = clean("region", 16).upper()
        ready = bool(
            recipient_name and recipient_address and recipient_zip
            and recipient_region == "MY"
        )
        vals = {
            "recipient_name": recipient_name,
            "recipient_phone": clean("phone", 40),
            "recipient_full_address": recipient_address,
            "recipient_city": clean("city", 128),
            "recipient_state": clean("state", 128),
            "recipient_zipcode": recipient_zip,
            "recipient_region": recipient_region,
            "recipient_ready": ready,
            "buyer_user_id": str(detail.get("buyer_user_id") or "").strip()[:64],
            "name": sn,
            "buyer_username": (str(detail.get("buyer_username") or "").strip()[:128]),
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
