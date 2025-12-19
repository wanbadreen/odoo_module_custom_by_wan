import json
import logging
import re

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    gdex_cn = fields.Char(string="GDEX AWB/CN", copy=False)
    gdex_status = fields.Char(string="GDEX Last Status")
    gdex_last_status_raw = fields.Text(string="GDEX Last Status Raw")
    gdex_last_sync_at = fields.Datetime(string="GDEX Last Sync At")
    gdex_last_error = fields.Text(string="GDEX Last Error")
    gdex_state = fields.Selection(
        [
            ("draft", "Draft"),
            ("created", "Created"),
            ("error", "Error"),
            ("delivered", "Delivered"),
        ],
        string="GDEX State",
        default="draft",
        copy=False,
    )

    def action_gdex_create_awb(self):
        for picking in self:
            picking._gdex_validate_ready()
            response = picking._gdex_call_create_consignment()
            cn = picking._gdex_extract_cn(response)
            if not cn:
                picking._gdex_handle_error(_("Missing CN in response."))
            picking.write(
                {
                    "gdex_cn": cn,
                    "gdex_state": "created",
                    "gdex_last_error": False,
                }
            )
            picking.message_post(body=_("GDEX AWB/CN created: %s", cn))
        return True

    def action_gdex_create_awb_batch(self):
        successes = []
        failures = []
        for picking in self:
            try:
                picking.action_gdex_create_awb()
                successes.append(picking.name)
            except UserError as exc:
                failures.append((picking.name, exc.args[0] if exc.args else str(exc)))
        summary = _("Created %s AWB, Failed %s", len(successes), len(failures))
        if failures:
            details = "\n".join([f"- {name}: {reason}" for name, reason in failures])
            raise UserError(f"{summary}\n{details}")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("GDEX AWB Creation"),
                "message": summary,
                "sticky": False,
                "type": "success",
            },
        }

    def _gdex_validate_ready(self):
        self.ensure_one()
        if self.picking_type_code != "outgoing":
            raise UserError(_("GDEX AWB can only be created for outgoing deliveries."))
        if self.state in ("done", "cancel"):
            raise UserError(_("Cannot create GDEX AWB for done or cancelled deliveries."))
        if self.gdex_cn:
            raise UserError(_("This delivery already has a GDEX AWB/CN."))
        partner = self.partner_shipping_id
        if not partner:
            raise UserError(_("Shipping address is required to create GDEX AWB."))
        missing = []
        if not partner.name:
            missing.append(_("Receiver Name"))
        if not partner.mobile and not partner.phone:
            missing.append(_("Receiver Mobile"))
        if not partner.email:
            missing.append(_("Receiver Email"))
        if not partner.street:
            missing.append(_("Receiver Address 1"))
        if not partner.zip:
            missing.append(_("Receiver Postcode"))
        if not partner.city:
            missing.append(_("Receiver City"))
        if missing:
            raise UserError(_("Missing required receiver fields: %s", ", ".join(missing)))
        if not partner.country_id or partner.country_id.name != "Malaysia":
            raise UserError(_("Receiver country must be Malaysia."))

    def _gdex_handle_error(self, message):
        self.ensure_one()
        self.write({"gdex_state": "error", "gdex_last_error": message})
        raise UserError(message)

    def _gdex_extract_cn(self, response):
        if not isinstance(response, dict):
            return False
        if response.get("s") != "success":
            error_message = response.get("e") or _("GDEX API error.")
            self._gdex_handle_error(error_message)
        cn_list = response.get("r") or []
        if isinstance(cn_list, list) and cn_list:
            return cn_list[0]
        return False

    def _gdex_call_create_consignment(self):
        self.ensure_one()
        company = self.company_id
        base_url = company.gdex_base_url or "https://myopenapi.gdexpress.com/api/demo/prime"
        account_no = company.gdex_account_no
        token = (
            company.gdex_api_token_sandbox
            if company.gdex_environment == "sandbox"
            else company.gdex_api_token_production
        )
        if not account_no:
            raise UserError(_("Please configure GDEX Account No in Settings."))
        if not token:
            raise UserError(_("Please configure GDEX API Token in Settings."))

        endpoint = f"{base_url}/CreateConsignment?accountNo={account_no}"
        payload = [self._gdex_prepare_payload()]
        headers = {"ApiToken": token, "Content-Type": "application/json"}

        _logger.info("GDEX CreateConsignment payload for %s: %s", self.name, payload)
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=20)
        except requests.RequestException as exc:
            _logger.exception("GDEX CreateConsignment request failed")
            self._gdex_handle_error(_("GDEX API connection error: %s", exc))
        if response.status_code == 401:
            self._gdex_handle_error(_("401 Access Denied"))
        if response.status_code != 200:
            self._gdex_handle_error(
                _("GDEX API error (HTTP %s): %s", response.status_code, response.text)
            )
        try:
            return response.json()
        except ValueError:
            self._gdex_handle_error(_("Invalid JSON response from GDEX."))

    def _gdex_prepare_payload(self):
        self.ensure_one()
        partner = self.partner_shipping_id
        mobile = partner.mobile or partner.phone or ""
        mobile = re.sub(r"[\s\-]", "", mobile)
        description = self._gdex_get_content_description()
        picking_name = self.name or ""
        return {
            "shipmentType": "Parcel",
            "totalPiece": 1,
            "shipmentWeight": 1,
            "shipmentLength": 1,
            "shipmentWidth": 1,
            "shipmentHeight": 1,
            "isDangerousGoods": False,
            "IsInsurance": False,
            "isCod": False,
            "codAmount": 0,
            "receiverName": partner.name or "",
            "receiverMobile": mobile,
            "receiverEmail": partner.email or "",
            "receiverAddress1": partner.street or "",
            "receiverAddress2": partner.street2 or "",
            "receiverAddress3": "",
            "receiverPostcode": partner.zip or "",
            "receiverCity": partner.city or "",
            "receiverState": partner.state_id.name if partner.state_id else "",
            "receiverCountry": "Malaysia",
            "orderID": picking_name,
            "doNumber1": picking_name[-20:],
            "content": description,
        }

    def _gdex_get_content_description(self):
        self.ensure_one()
        names = []
        for move in self.move_ids_without_package:
            if move.product_id:
                names.append(move.product_id.display_name)
        description = ", ".join(names).strip()
        if not description:
            description = "Goods"
        return description[:512]

    def _gdex_call_tracking(self, awb):
        self.ensure_one()
        company = self.company_id
        base_url = company.gdex_base_url or "https://myopenapi.gdexpress.com/api/demo/prime"
        token = (
            company.gdex_api_token_sandbox
            if company.gdex_environment == "sandbox"
            else company.gdex_api_token_production
        )
        if not token:
            raise UserError(_("Please configure GDEX API Token in Settings."))
        endpoint = f"{base_url}/GetLastShipmentStatus"
        headers = {"ApiToken": token, "Content-Type": "application/json"}
        payloads = [{"cnNo": awb}, {"awb": awb}]

        last_error = None
        for payload in payloads:
            try:
                response = requests.post(endpoint, headers=headers, json=payload, timeout=20)
            except requests.RequestException as exc:
                last_error = _("GDEX tracking POST error: %s", exc)
                _logger.warning("GDEX tracking POST failed: %s", exc)
                continue
            if response.status_code == 200:
                return response, payload
            last_error = _("GDEX tracking POST failed (HTTP %s)", response.status_code)

        for payload in payloads:
            try:
                response = requests.get(endpoint, headers=headers, params=payload, timeout=20)
            except requests.RequestException as exc:
                last_error = _("GDEX tracking GET error: %s", exc)
                _logger.warning("GDEX tracking GET failed: %s", exc)
                continue
            if response.status_code == 200:
                return response, payload
            last_error = _("GDEX tracking GET failed (HTTP %s)", response.status_code)

        raise UserError(last_error or _("GDEX tracking failed."))

    def _gdex_extract_status(self, payload):
        if isinstance(payload, dict):
            for key in ("status", "shipmentStatus", "lastStatus", "scanStatus"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
            result = payload.get("r") or payload.get("result")
            if isinstance(result, dict):
                return self._gdex_extract_status(result)
            if isinstance(result, list) and result:
                for item in result:
                    if isinstance(item, dict):
                        status = self._gdex_extract_status(item)
                        if status:
                            return status
        return False

    def _gdex_is_delivered(self, status, raw_text):
        if status and "delivered" in status.lower():
            return True
        if raw_text and "delivered" in raw_text.lower():
            return True
        return False

    def _gdex_sync_last_status(self):
        self.ensure_one()
        if not self.gdex_cn:
            return
        try:
            response, payload = self._gdex_call_tracking(self.gdex_cn)
            try:
                data = response.json()
                raw = json.dumps(data, ensure_ascii=False)
            except ValueError:
                data = {}
                raw = response.text
            status = self._gdex_extract_status(data) or ""
            delivered = self._gdex_is_delivered(status, raw)
            values = {
                "gdex_status": status,
                "gdex_last_status_raw": raw,
                "gdex_last_sync_at": fields.Datetime.now(),
                "gdex_last_error": False,
            }
            if delivered:
                values["gdex_state"] = "delivered"
            self.write(values)
            _logger.info("GDEX tracking sync for %s with payload %s", self.name, payload)
        except UserError as exc:
            self.write(
                {
                    "gdex_last_sync_at": fields.Datetime.now(),
                    "gdex_last_error": exc.args[0] if exc.args else str(exc),
                    "gdex_state": "error",
                }
            )
            _logger.warning("GDEX tracking failed for %s: %s", self.name, exc)

    @api.model
    def _gdex_cron_sync_status(self):
        domain = [
            ("gdex_cn", "!=", False),
            ("state", "not in", ["done", "cancel"]),
            ("gdex_state", "in", ["created", "error"]),
        ]
        pickings = self.search(domain)
        for picking in pickings:
            picking._gdex_sync_last_status()
