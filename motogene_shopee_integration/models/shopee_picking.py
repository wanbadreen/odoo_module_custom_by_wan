import base64
import io
import logging
import time

from pypdf import PdfReader, PdfWriter

import requests

from odoo import fields, models, _
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
        if "mori_awb_no" not in self._fields:
            raise UserError(_("Operations AWB field 'mori_awb_no' is missing."))
        if self.mori_awb_no and self.mori_awb_no.strip() != str(tracking).strip():
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
        self._motogene_shopee_sync_main_awb(tracking)
        self.write({"motogene_shopee_awb_attachment_id": attachment.id})
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
