import base64
import io
import logging
import time

from pypdf import PdfReader, PdfWriter

import requests

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    motogene_shopee_tracking_number = fields.Char(
        string="Shopee Tracking Number", readonly=True, copy=False,
    )
    motogene_shopee_awb_attachment_id = fields.Many2one(
        "ir.attachment", string="Shopee AWB PDF", readonly=True, copy=False,
    )
    motogene_shopee_is_delivery = fields.Boolean(
        compute="_compute_motogene_shopee_is_delivery",
    )

    def _compute_motogene_shopee_is_delivery(self):
        for picking in self:
            so = picking.sale_id
            picking.motogene_shopee_is_delivery = bool(
                picking.picking_type_code == "outgoing"
                and so and so.motogene_shopee_channel == "shopee"
            )

    motogene_shopee_awb_needs_sync = fields.Boolean(
        string="Shopee AWB No Needs Sync",
        compute="_compute_motogene_shopee_awb_needs_sync",
    )

    def _compute_motogene_shopee_awb_needs_sync(self):
        for picking in self:
            tracking = (picking.motogene_shopee_tracking_number or "").strip()
            current = (
                (picking.mori_awb_no or "").strip()
                if "mori_awb_no" in picking._fields else ""
            )
            picking.motogene_shopee_awb_needs_sync = bool(
                picking.motogene_shopee_is_delivery
                and picking.motogene_shopee_awb_attachment_id
                and tracking and tracking != current
                and "mori_awb_no" in picking._fields
            )

    motogene_shopee_shipment_state = fields.Selection([
        ("not_sent", "Not Arranged in Odoo"),
        ("requested", "Requested via Odoo"),
        ("uncertain", "Uncertain - Verify Shopee"),
    ], default="not_sent", readonly=True, copy=False, string="Shopee Shipment Request")
    motogene_shopee_shipping_method = fields.Selection([
        ("pickup", "Pickup"),
        ("dropoff", "Drop-off"),
    ], readonly=True, copy=False, string="Shopee Shipping Method")
    motogene_shopee_document_state = fields.Selection([
        ("not_created", "Not Requested in Odoo"),
        ("requested", "Task Requested"),
        ("processing", "Processing"),
        ("ready", "Ready"),
        ("failed", "Failed"),
        ("uncertain", "Uncertain - Verify Shopee"),
    ], readonly=True, copy=False, default="not_created", string="Shopee AWB Task")

    def _motogene_shopee_notify(self, title, message, level="info"):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title, "message": message,
                "type": level, "sticky": True,
            },
        }

    def _motogene_shopee_api_post(self, shop, path, payload):
        """Return (response_body, error). No automatic retries on mutating Shopee APIs."""
        shop._refresh_if_needed()
        ts = int(time.time())
        params = {
            "partner_id": shop._partner_id_int(),
            "timestamp": ts,
            "access_token": shop.access_token,
            "shop_id": int(shop.shop_id),
            "sign": shop._signature(path, ts, shop.access_token, shop.shop_id),
        }
        try:
            response = requests.post(
                shop._host() + path, params=params, json=payload, timeout=25,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError):
            _logger.warning("Shopee mutating request outcome uncertain for DO %s", self.id)
            return {}, "transport"
        if body.get("error"):
            return body, str(body["error"])[:150]
        return body, None

    motogene_shopee_last_progress_check = fields.Datetime(
        string="Shopee AWB Last Checked", readonly=True, copy=False,
    )
    motogene_shopee_progress_checks = fields.Integer(
        string="Shopee AWB Check Count", readonly=True, copy=False, default=0,
    )

    @api.model
    def _cron_motogene_shopee_awb_progress(self):
        """Sandbox only, capped. Never arrange shipment again; stop on uncertainty."""
        if self.env["ir.config_parameter"].sudo().get_param(
            "motogene_shopee.enable_awb_auto_progress", "0"
        ) != "1":
            return
        pending = self.sudo().search([
            ("picking_type_code", "=", "outgoing"),
            ("state", "in", ["confirmed", "assigned", "waiting"]),
            ("motogene_shopee_shipment_state", "=", "requested"),
            ("motogene_shopee_awb_attachment_id", "=", False),
            ("motogene_shopee_document_state", "in",
             ["not_created", "requested", "processing", "ready"]),
            ("sale_id.motogene_shopee_channel", "=", "shopee"),
            ("sale_id.motogene_shopee_preview_id.shop_id.environment", "=", "sandbox"),
        ], order="motogene_shopee_last_progress_check asc, id asc", limit=10)
        for picking in pending:
            started = time.monotonic()
            try:
                with self.env.cr.savepoint():
                    # Do not compete with another cron/manual progress operation.
                    self.env.cr.execute(
                        "SELECT pg_try_advisory_xact_lock(%s, id) FROM stock_picking "
                        "WHERE id = %s", [741230, picking.id]
                    )
                    if not self.env.cr.fetchone()[0]:
                        continue
                    picking.invalidate_recordset([
                        "motogene_shopee_awb_attachment_id",
                        "motogene_shopee_shipment_state",
                        "motogene_shopee_document_state",
                    ])
                    if (picking.motogene_shopee_awb_attachment_id
                            or picking.motogene_shopee_shipment_state != "requested"
                            or picking.motogene_shopee_document_state in ("uncertain", "failed")):
                        continue
                    picking.action_motogene_shopee_check_progress()
                    picking.write({
                        "motogene_shopee_last_progress_check": fields.Datetime.now(),
                        "motogene_shopee_progress_checks": picking.motogene_shopee_progress_checks + 1,
                    })
                    _logger.info(
                        "Shopee AWB progress DO=%s state=%s attached=%s elapsed=%.2fs",
                        picking.name, picking.motogene_shopee_document_state,
                        bool(picking.motogene_shopee_awb_attachment_id),
                        time.monotonic() - started,
                    )
            except Exception:
                _logger.exception("Shopee AWB auto progress failed for DO %s", picking.name)

    def action_motogene_shopee_prepare(self):
        """One entry point: choose shipment method, or advance an arranged shipment."""
        self.ensure_one()
        self._motogene_shopee_check_awb_access()
        if self.motogene_shopee_awb_attachment_id:
            return self._motogene_shopee_notify(
                _("Shopee AWB Ready"),
                _("AWB is already attached. Click Print Shopee AWB."),
                "success",
            )
        if self.motogene_shopee_shipment_state == "uncertain":
            return self._motogene_shopee_notify(
                _("Shipment status uncertain"),
                _("Check Shopee Seller Centre before proceeding; no new shipment request was sent."),
                "warning",
            )
        if self.motogene_shopee_shipment_state == "not_sent":
            # Existing wizard verifies live Pickup/Drop-off parameters before writing.
            return self.action_motogene_shopee_arrange_shipment()
        return self.action_motogene_shopee_check_progress()

    def action_motogene_shopee_check_progress(self):
        """Bounded progress step: no polling loop and no repeated shipment arrangement."""
        self.ensure_one()
        sale, preview, shop = self._motogene_shopee_check_awb_access()
        # Serialize UI and background progress for this DO, including first task creation.
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", [741230, self.id]
        )
        self.invalidate_recordset([
            "motogene_shopee_awb_attachment_id",
            "motogene_shopee_shipment_state",
            "motogene_shopee_document_state",
        ])
        if self.motogene_shopee_awb_attachment_id:
            return self._motogene_shopee_notify(
                _("Shopee AWB Ready"), _("PDF is attached. Click Print Shopee AWB."), "success"
            )
        if self.motogene_shopee_shipment_state == "uncertain":
            return self._motogene_shopee_notify(
                _("Manual shipment verification needed"),
                _("The arrange result was uncertain. Verify in Seller Centre first."),
                "warning",
            )
        if self.motogene_shopee_shipment_state == "not_sent":
            return self.action_motogene_shopee_arrange_shipment()

        order_sn = sale.motogene_shopee_order_ref or preview.name
        tracking = shop._read_only_api_get(
            "/api/v2/logistics/get_tracking_number", {"order_sn": order_sn}
        ).get("tracking_number")
        if not tracking:
            return self._motogene_shopee_notify(
                _("Shopee AWB Pending"),
                _("Shipment arranged. Shopee tracking is not available yet. Click Check Shipment Progress later."),
                "warning",
            )
        tracking = str(tracking).strip()
        current = (self.mori_awb_no or "").strip() if "mori_awb_no" in self._fields else ""
        if current and current != tracking:
            saved = (self.motogene_shopee_tracking_number or "").strip()
            # Sandbox sometimes changes its provisional number after arranging.
            # Only reconcile a value previously mirrored in BOTH our fields.
            # Never replace a printed/attached label or another carrier reference.
            safe_to_reconcile = (
                preview.shop_id.environment == "sandbox"
                and self.state not in ("done", "cancel")
                and self.motogene_shopee_shipment_state == "requested"
                and self.motogene_shopee_document_state in ("requested", "processing", "ready")
                and bool(saved) and saved == current
                and not self.motogene_shopee_awb_attachment_id
                and not self.carrier_tracking_ref
            )
            if safe_to_reconcile:
                confirmation = shop._read_only_api_get(
                    "/api/v2/logistics/get_tracking_number", {"order_sn": order_sn}
                ).get("tracking_number")
                safe_to_reconcile = str(confirmation or "").strip() == tracking
            if not safe_to_reconcile:
                return self._motogene_shopee_notify(
                    _("AWB number conflict"),
                    _("Saved AWB differs from Shopee tracking. Manual review required."),
                    "warning",
                )
            _logger.warning(
                "Shopee sandbox tracking reconciliation on DO %s: %s -> %s",
                self.name, current, tracking,
            )
        values = {"motogene_shopee_tracking_number": tracking}
        if "mori_awb_no" in self._fields:
            values["mori_awb_no"] = tracking
        self.write(values)

        state = self.motogene_shopee_document_state
        if state in ("uncertain", "failed"):
            return self._motogene_shopee_notify(
                _("AWB needs review"),
                _("Previous AWB task is uncertain or failed. Use advanced tools to investigate."),
                "warning",
            )

        status, error = self._motogene_shopee_get_document_status(shop, order_sn)
        if error:
            return self._motogene_shopee_notify(
                _("Shopee AWB status unavailable"), str(error)[:180], "warning"
            )
        if status == "READY":
            self.write({"motogene_shopee_document_state": "ready"})
            return self.action_motogene_shopee_retrieve_awb()
        if status == "PROCESSING":
            self.write({"motogene_shopee_document_state": "processing"})
            return self._motogene_shopee_notify(
                _("Shopee AWB Pending"),
                _("Shopee is generating the document. Click Check Shipment Progress later."),
                "warning",
            )
        if status == "FAILED":
            self.write({"motogene_shopee_document_state": "failed"})
            return self._motogene_shopee_notify(
                _("Shopee AWB failed"),
                _("Document task failed at Shopee. Review before creating a new task."),
                "warning",
            )
        if state in ("requested", "processing", "ready"):
            # A missing response must never trigger a second create request.
            return self._motogene_shopee_notify(
                _("Shopee AWB Pending"),
                _("Task was already requested. Waiting for Shopee document status."),
                "warning",
            )

        # Only for first request when no prior task is registered.
        result = self.action_motogene_shopee_generate_document()
        if self.motogene_shopee_document_state != "requested":
            return result

        status, error = self._motogene_shopee_get_document_status(shop, order_sn)
        if error:
            return self._motogene_shopee_notify(
                _("AWB task submitted"),
                _("Shopee accepted the task; check progress later. Status detail: %s") % str(error)[:120],
                "warning",
            )
        if status == "READY":
            self.write({"motogene_shopee_document_state": "ready"})
            return self.action_motogene_shopee_retrieve_awb()
        if status == "FAILED":
            self.write({"motogene_shopee_document_state": "failed"})
            return self._motogene_shopee_notify(
                _("Shopee AWB failed"), _("Document generation failed. Check advanced tools."), "warning"
            )
        self.write({"motogene_shopee_document_state": "processing" if status == "PROCESSING" else "requested"})
        return self._motogene_shopee_notify(
            _("Shopee AWB Pending"),
            _("Shipment is arranged and AWB task is submitted. Click Check Shipment Progress later."),
            "warning",
        )

    def action_motogene_shopee_arrange_shipment(self):
        self.ensure_one()
        sale, preview, shop = self._motogene_shopee_check_awb_access()
        if self.motogene_shopee_shipment_state != "not_sent":
            raise UserError(_("Shipment already requested or uncertain. Do not arrange again."))
        order_sn = sale.motogene_shopee_order_ref or preview.name
        tracking = shop._read_only_api_get(
            "/api/v2/logistics/get_tracking_number", {"order_sn": order_sn}
        ).get("tracking_number")
        if tracking:
            self.write({
                "motogene_shopee_tracking_number": str(tracking),
                "motogene_shopee_shipment_state": "requested",
            })
            return self._motogene_shopee_notify(
                _("Shipment already arranged"),
                _("Shopee returned tracking; no shipment request sent."),
            )
        shipping = shop._read_only_api_get(
            "/api/v2/logistics/get_shipping_parameter", {"order_sn": order_sn}
        )
        needed = shipping.get("info_needed") or {}
        vals = {
            "picking_id": self.id, "order_sn": order_sn,
            "pickup_available": "pickup" in needed,
            "dropoff_available": "dropoff" in needed,
            "dropoff_requires_details": bool(needed.get("dropoff")),
            "warning_text": _(
                "Sandbox only. Choosing Pickup or Drop-off SENDS a shipment request. "
                "Use only a valid option returned by Shopee. Never repeat after timeout."
            ),
        }
        options = []
        for address in (shipping.get("pickup") or {}).get("address_list") or []:
            for slot in address.get("time_slot_list") or []:
                if address.get("address_id") and slot.get("pickup_time_id"):
                    options.append((0, 0, {
                        "address_id_value": int(address["address_id"]),
                        "pickup_time_id_value": str(slot["pickup_time_id"]),
                        "display_slot": str(slot.get("time_text") or slot.get("date") or ""),
                        "address_label": ", ".join(filter(None, [
                            str(address.get("city") or ""),
                            str(address.get("state") or ""),
                        ])) or _("Address ID %s") % address["address_id"],
                    }))
        vals["option_ids"] = options
        wizard = self.env["motogene.shopee.shipment.wizard"].create(vals)
        return {
            "type": "ir.actions.act_window", "res_model": wizard._name,
            "res_id": wizard.id, "view_mode": "form", "target": "new",
            "name": _("Arrange Shopee Shipment (Sandbox)"),
        }

    def action_motogene_shopee_sync_tracking(self):
        self.ensure_one()
        sale, preview, shop = self._motogene_shopee_check_awb_access()
        tracking = shop._read_only_api_get(
            "/api/v2/logistics/get_tracking_number",
            {"order_sn": sale.motogene_shopee_order_ref or preview.name}
        ).get("tracking_number")
        if not tracking:
            return self._motogene_shopee_notify(
                _("Tracking pending"), _("Shopee has not assigned tracking yet."), "warning"
            )
        tracking = str(tracking).strip()
        if ("mori_awb_no" in self._fields and self.mori_awb_no
                and self.mori_awb_no.strip() != tracking):
            raise UserError(_("Existing AWB No differs from Shopee tracking."))
        vals = {
            "motogene_shopee_tracking_number": tracking,
            "motogene_shopee_shipment_state": "requested",
        }
        if "mori_awb_no" in self._fields:
            vals["mori_awb_no"] = tracking
        self.write(vals)
        return self._motogene_shopee_notify(
            _("Tracking synchronized"), tracking, "success"
        )

    def _motogene_shopee_get_document_status(self, shop, order_sn):
        body, error = self._motogene_shopee_api_post(
            shop, "/api/v2/logistics/get_shipping_document_result",
            {"order_list": [{
                "order_sn": order_sn,
                "shipping_document_type": "THERMAL_AIR_WAYBILL",
            }]},
        )
        if error:
            # Shopee's pre-generation result check can return this specific code
            # when no shipping document has yet been created. Treat it as no
            # existing task, NOT as a reason to retry a previously sent task.
            if error == "logistics.shipping_document_should_print_first":
                return None, None
            return None, error
        results = (body.get("response") or {}).get("result_list") or []
        item = next((r for r in results if r.get("order_sn") == order_sn), None)
        if not item:
            # No existing task in the Shopee response: allow first manual creation.
            return None, None
        if item.get("fail_error") or item.get("error"):
            return None, str(item.get("fail_error") or item.get("error"))[:150]
        return item.get("status"), None

    def action_motogene_shopee_check_document(self):
        self.ensure_one()
        sale, preview, shop = self._motogene_shopee_check_awb_access()
        status, error = self._motogene_shopee_get_document_status(
            shop, sale.motogene_shopee_order_ref or preview.name
        )
        if error:
            return self._motogene_shopee_notify(
                _("AWB status unavailable"), error, "warning"
            )
        state = {"READY": "ready", "PROCESSING": "processing", "FAILED": "failed"}.get(status)
        if state:
            self.write({"motogene_shopee_document_state": state})
        return self._motogene_shopee_notify(
            _("AWB Document Status"),
            status or _("No status returned"),
            "success" if status == "READY" else "info",
        )

    def action_motogene_shopee_generate_document(self):
        self.ensure_one()
        sale, preview, shop = self._motogene_shopee_check_awb_access()
        if self.motogene_shopee_document_state in ("requested", "processing", "ready", "uncertain"):
            raise UserError(_("Document already requested or status uncertain. Check status first."))
        order_sn = sale.motogene_shopee_order_ref or preview.name
        tracking = shop._read_only_api_get(
            "/api/v2/logistics/get_tracking_number", {"order_sn": order_sn}
        ).get("tracking_number")
        if not tracking:
            raise UserError(_("Tracking not available; arrange shipment first."))
        status, status_error = self._motogene_shopee_get_document_status(shop, order_sn)
        if status == "READY":
            self.write({"motogene_shopee_document_state": "ready"})
            return self._motogene_shopee_notify(
                _("AWB already ready"), _("Use Retrieve Shopee AWB on the DO."), "success"
            )
        if status == "PROCESSING":
            self.write({"motogene_shopee_document_state": "processing"})
            return self._motogene_shopee_notify(
                _("AWB already processing"), _("Check document status later.")
            )
        if status_error:
            # Some sandbox orders report no document task until one is created;
            # do not automatically assume other API errors mean safe to create.
            raise UserError(_("Cannot verify existing AWB task: %s") % status_error)
        if self.motogene_shopee_document_state == "failed":
            raise UserError(_("Previous document task failed. Review before retry."))
        parameter, error = self._motogene_shopee_api_post(
            shop, "/api/v2/logistics/get_shipping_document_parameter",
            {"order_list": [{"order_sn": order_sn}]},
        )
        if error:
            raise UserError(_("Cannot retrieve AWB document options: %s") % error)
        options = (parameter.get("response") or {}).get("result_list") or []
        item = next((x for x in options if x.get("order_sn") == order_sn), None)
        if not item or item.get("fail_error"):
            raise UserError(_("Shopee did not supply valid AWB options."))
        allowed = item.get("selectable_shipping_document_type") or []
        if "THERMAL_AIR_WAYBILL" not in allowed:
            raise UserError(_("THERMAL_AIR_WAYBILL is not supported by this order."))
        body, error = self._motogene_shopee_api_post(
            shop, "/api/v2/logistics/create_shipping_document",
            {"order_list": [{
                "order_sn": order_sn,
                "tracking_number": str(tracking),
                "shipping_document_type": "THERMAL_AIR_WAYBILL",
            }]},
        )
        if error == "transport":
            self.write({"motogene_shopee_document_state": "uncertain"})
            return self._motogene_shopee_notify(
                _("AWB task outcome uncertain"),
                _("Check status before taking any action. Do not create task again."),
                "warning",
            )
        if error:
            return self._motogene_shopee_notify(
                _("Shopee rejected AWB task"), error, "warning"
            )
        results = (body.get("response") or {}).get("result_list") or []
        result = next((x for x in results if x.get("order_sn") == order_sn), None)
        if not result:
            self.write({"motogene_shopee_document_state": "uncertain"})
            return self._motogene_shopee_notify(
                _("AWB task outcome uncertain"), _("No item result returned. Check status."), "warning"
            )
        if result.get("fail_error") or result.get("error"):
            return self._motogene_shopee_notify(
                _("AWB task rejected"), str(result.get("fail_error") or result.get("error"))[:200], "warning"
            )
        self.write({
            "motogene_shopee_tracking_number": str(tracking),
            "motogene_shopee_document_state": "requested",
        })
        return self._motogene_shopee_notify(
            _("AWB task accepted"), _("Check AWB Document Status before retrieving PDF."), "success"
        )

    def _motogene_shopee_check_awb_access(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Sandbox Shopee AWB testing is limited to administrators."))
        if not self.motogene_shopee_is_delivery:
            raise UserError(_("This is not an outgoing Shopee Delivery Order."))
        if self.state == "cancel":
            raise UserError(_("Cannot retrieve an AWB for a cancelled Delivery Order."))
        sale = self.sale_id
        preview = sale.motogene_shopee_preview_id
        if not preview or preview.shop_id.environment != "sandbox":
            raise UserError(_("Manual Shopee AWB test is limited to linked sandbox orders."))
        if sale.company_id != self.company_id:
            raise UserError(_("Sales Order and Delivery Order companies do not match."))
        return sale, preview, preview.shop_id.sudo()

    def _motogene_shopee_sync_main_awb(self, tracking):
        """Populate the pre-existing Operations AWB field, never overwriting other AWBs."""
        self.ensure_one()
        if "mori_awb_no" not in self._fields:
            raise UserError(_("Operations AWB field 'mori_awb_no' is missing. Check installed modules."))
        tracking = str(tracking or "").strip()
        if not tracking:
            raise UserError(_("Shopee tracking number is unavailable."))
        existing = (self.mori_awb_no or "").strip()
        if existing and existing != tracking:
            raise UserError(_(
                "AWB No already contains a different number (%s). "
                "Refusing to overwrite it."
            ) % existing)
        self.write({
            "motogene_shopee_tracking_number": tracking,
            "mori_awb_no": tracking,
        })

    def action_motogene_shopee_sync_awb_no(self):
        """For sandbox DOs retrieved before AWB field synchronization was added."""
        self.ensure_one()
        self._motogene_shopee_check_awb_access()
        if not self.motogene_shopee_awb_attachment_id:
            raise UserError(_("Retrieve Shopee AWB first."))
        if not self.motogene_shopee_tracking_number:
            raise UserError(_("Shopee tracking number is missing on this Delivery Order."))
        self._motogene_shopee_sync_main_awb(self.motogene_shopee_tracking_number)
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }

    def action_motogene_shopee_retrieve_awb(self):
        self.ensure_one()
        sale, preview, shop = self._motogene_shopee_check_awb_access()
        if self.motogene_shopee_awb_attachment_id:
            raise UserError(_("Shopee AWB is already attached. Use Print Shopee AWB."))
        order_sn = sale.motogene_shopee_order_ref or preview.name
        # This manual action only downloads an AWB already made READY in Shopee.
        # It NEVER calls ship_order or create_shipping_document.
        tracking_data = shop._read_only_api_get(
            "/api/v2/logistics/get_tracking_number", {"order_sn": order_sn}
        )
        tracking = tracking_data.get("tracking_number")
        if not tracking:
            raise UserError(_("Shopee has not assigned a tracking number yet."))
        path = "/api/v2/logistics/download_shipping_document"
        shop._refresh_if_needed()
        ts = int(time.time())
        params = {
            "partner_id": shop._partner_id_int(),
            "timestamp": ts,
            "access_token": shop.access_token,
            "shop_id": int(shop.shop_id),
            "sign": shop._signature(path, ts, shop.access_token, shop.shop_id),
        }
        payload = {
            "order_list": [{"order_sn": order_sn}],
            "shipping_document_type": "THERMAL_AIR_WAYBILL",
        }
        try:
            response = requests.post(
                shop._host() + path, params=params, json=payload, timeout=30,
            )
            response.raise_for_status()
            binary = response.content
        except requests.RequestException:
            _logger.warning("Shopee AWB download transport failed for picking %s", self.id)
            raise UserError(_("Shopee AWB download failed. Check Odoo logs."))
        if not binary.startswith(b"%PDF-"):
            raise UserError(_("Shopee returned a non-PDF response. Verify document status READY first."))
        if len(binary) > 5 * 1024 * 1024:
            raise UserError(_("Shopee AWB PDF exceeds the 5 MB test limit."))
        # Validate the Operations AWB field before creating an attachment.
        if ("mori_awb_no" in self._fields
                and self.mori_awb_no
                and self.mori_awb_no.strip() != str(tracking).strip()):
            raise UserError(_("Existing AWB No differs from Shopee tracking; refusing to overwrite."))
        attachment = self.env["ir.attachment"].sudo().create({
            "name": "Shopee_AWB_%s.pdf" % order_sn,
            "type": "binary",
            "datas": base64.b64encode(binary),
            "mimetype": "application/pdf",
            "res_model": "stock.picking",
            "res_id": self.id,
            "public": False,
        })
        vals = {
            "motogene_shopee_tracking_number": str(tracking),
            "motogene_shopee_awb_attachment_id": attachment.id,
        }
        if "mori_awb_no" in self._fields:
            vals["mori_awb_no"] = str(tracking)
        else:
            _logger.warning("Optional mori_awb_no field missing on picking %s; Shopee tracking kept in module field", self.id)
        self.write(vals)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Shopee AWB saved"),
                "message": _("Thermal AWB attached to this Delivery Order."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_motogene_shopee_print_awb(self):
        self.ensure_one()
        self._motogene_shopee_check_awb_access()
        attachment = self.motogene_shopee_awb_attachment_id
        if not attachment or attachment.res_model != "stock.picking" or attachment.res_id != self.id:
            raise UserError(_("No Shopee AWB is attached. Retrieve the AWB first."))
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "new",
        }

    def action_motogene_shopee_bulk_print_awb(self):
        """Manually merge previously attached Shopee PDFs for selected outgoing DOs."""
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Sandbox Shopee bulk printing is limited to administrators."))
        if not self:
            raise UserError(_("Select at least one Shopee Delivery Order."))
        if len(self) > 50:
            raise UserError(_("Select no more than 50 Delivery Orders per batch."))

        writer = PdfWriter()
        printed = []
        skipped = []
        for picking in self.sorted("id"):
            if not picking.motogene_shopee_is_delivery or picking.state == "cancel":
                skipped.append(picking.name)
                continue
            picking._motogene_shopee_check_awb_access()
            attachment = picking.motogene_shopee_awb_attachment_id
            if (not attachment or attachment.res_model != "stock.picking"
                    or attachment.res_id != picking.id or attachment.mimetype != "application/pdf"):
                skipped.append(picking.name)
                continue
            binary = base64.b64decode(attachment.sudo().datas or b"")
            if not binary.startswith(b"%PDF-") or len(binary) > 5 * 1024 * 1024:
                skipped.append(picking.name)
                continue
            try:
                reader = PdfReader(io.BytesIO(binary))
                if not reader.pages or len(reader.pages) > 10:
                    skipped.append(picking.name)
                    continue
                for page in reader.pages:
                    writer.add_page(page)
            except Exception:
                _logger.warning("Invalid Shopee AWB attachment on picking %s", picking.id)
                raise UserError(_("Invalid PDF on %s; bulk print stopped.") % picking.name)
            printed.append(picking.name)

        if not printed:
            raise UserError(_("No valid attached Shopee AWBs found for the selected DOs."))
        output = io.BytesIO()
        writer.write(output)
        pdf = output.getvalue()
        if len(pdf) > 25 * 1024 * 1024:
            raise UserError(_("Combined Shopee AWB PDF exceeds 25 MB. Reduce batch size."))
        filename = "Shopee_AWB_Bulk_%s_DOs.pdf" % len(printed)
        attachment = self.env["ir.attachment"].sudo().create({
            "name": filename,
            "type": "binary",
            "datas": base64.b64encode(pdf),
            "mimetype": "application/pdf",
            "res_model": "res.users",
            "res_id": self.env.user.id,
            "public": False,
        })
        # PDF contains only valid selected DOs; warn about exclusions in server logs
        # and the filename when selection differs.
        if skipped:
            _logger.info("Shopee bulk AWB: printed %s, skipped %s", printed, skipped)
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "new",
        }
