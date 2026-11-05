import logging
import time

import requests

from odoo import fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ShopeeShipmentOption(models.TransientModel):
    _name = "motogene.shopee.shipment.option"
    _description = "Shopee Pickup Option"

    wizard_id = fields.Many2one("motogene.shopee.shipment.wizard", required=True, ondelete="cascade")
    address_id_value = fields.Integer(readonly=True)
    pickup_time_id_value = fields.Char(readonly=True)
    display_slot = fields.Char(readonly=True)
    address_label = fields.Char(readonly=True)

    def action_arrange_pickup(self):
        self.ensure_one()
        return self.wizard_id._arrange("pickup", self)


class ShopeeShipmentWizard(models.TransientModel):
    _name = "motogene.shopee.shipment.wizard"
    _description = "Arrange Shopee Shipment (Manual)"

    picking_id = fields.Many2one("stock.picking", required=True, readonly=True)
    order_sn = fields.Char(readonly=True)
    pickup_available = fields.Boolean(readonly=True)
    dropoff_available = fields.Boolean(readonly=True)
    dropoff_requires_details = fields.Boolean(readonly=True)
    option_ids = fields.One2many("motogene.shopee.shipment.option", "wizard_id", readonly=True)
    warning_text = fields.Text(readonly=True)

    def _arrange(self, method, option=None):
        self.ensure_one()
        picking = self.picking_id
        sale, preview, shop = picking._motogene_shopee_check_awb_access()
        order_sn = sale.motogene_shopee_order_ref or preview.name
        if self.order_sn != order_sn:
            raise UserError(_("Shopee Order SN changed. Open the shipment wizard again."))
        if picking.motogene_shopee_shipment_state != "not_sent":
            raise UserError(_("Shipment already requested or outcome uncertain. Verify Shopee before retrying."))

        # Lock DO row to protect against two simultaneous button clicks.
        self.env.cr.execute(
            "SELECT id FROM stock_picking WHERE id = %s FOR UPDATE NOWAIT", [picking.id]
        )
        picking.invalidate_recordset(["motogene_shopee_shipment_state"])
        if picking.motogene_shopee_shipment_state != "not_sent":
            raise UserError(_("Shipment was already requested. Reopen the Delivery Order."))

        current_tracking = shop._read_only_api_get(
            "/api/v2/logistics/get_tracking_number", {"order_sn": order_sn}
        ).get("tracking_number")
        if current_tracking:
            picking.write({
                "motogene_shopee_tracking_number": str(current_tracking),
                "motogene_shopee_shipment_state": "requested",
            })
            return picking.action_motogene_shopee_check_progress()

        shipping = shop._read_only_api_get(
            "/api/v2/logistics/get_shipping_parameter", {"order_sn": order_sn}
        )
        needed = shipping.get("info_needed") or {}
        if method == "pickup":
            if "pickup" not in needed or not option:
                raise UserError(_("Pickup is no longer offered by Shopee. Refresh shipment options."))
            valid = any(
                int(a.get("address_id") or 0) == option.address_id_value
                and any(
                    str(slot.get("pickup_time_id") or "") == option.pickup_time_id_value
                    for slot in a.get("time_slot_list") or []
                )
                for a in (shipping.get("pickup") or {}).get("address_list") or []
            )
            if not valid:
                raise UserError(_("Pickup slot expired. Reopen shipment wizard."))
            detail = {
                "pickup": {
                    "address_id": option.address_id_value,
                    "pickup_time_id": option.pickup_time_id_value,
                }
            }
        elif method == "dropoff":
            if "dropoff" not in needed:
                raise UserError(_("Drop-off is not offered by Shopee for this order."))
            if needed.get("dropoff"):
                raise UserError(_("Drop-off requires additional inputs not yet supported. No request sent."))
            detail = {"dropoff": {}}
        else:
            raise UserError(_("Unsupported shipping method."))

        # Make request-state durable on uncertain HTTP/API results without retries.
        # Never post a second request automatically.
        response, error = picking._motogene_shopee_api_post(
            shop, "/api/v2/logistics/ship_order",
            {"order_sn": order_sn, **detail},
        )
        if error == "transport":
            picking.write({"motogene_shopee_shipment_state": "uncertain"})
            return picking._motogene_shopee_notify(
                _("Shipment result uncertain"),
                _("The API did not confirm the request. Check Seller Centre; do not send again."),
                "warning",
            )
        if error:
            return picking._motogene_shopee_notify(
                _("Shopee rejected shipment"),
                str(error)[:200],
                "warning",
            )
        picking.write({
            "motogene_shopee_shipment_state": "requested",
            "motogene_shopee_shipping_method": method,
        })
        return picking.action_motogene_shopee_check_progress()

    def action_arrange_dropoff(self):
        self.ensure_one()
        if not self.dropoff_available or self.dropoff_requires_details:
            raise UserError(_("Drop-off is not available for this order without additional parameters."))
        return self._arrange("dropoff")
