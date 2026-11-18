from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare


class MotogeneFedexQuotePackage(models.TransientModel):
    _name = "motogene.fedex.quote.package"
    _description = "FedEx Quotation Package"

    wizard_id = fields.Many2one(
        "motogene.fedex.quote.wizard", required=True, ondelete="cascade"
    )
    weight = fields.Float(string="Actual Weight (kg)", required=True, default=1.0)
    length = fields.Integer(required=True, default=40)
    width = fields.Integer(required=True, default=30)
    height = fields.Integer(required=True, default=25)


class MotogeneFedexQuoteWizard(models.TransientModel):
    _name = "motogene.fedex.quote.wizard"
    _description = "FedEx Shipping Quotation Wizard"

    order_id = fields.Many2one("sale.order", required=True, readonly=True)
    partner_shipping_id = fields.Many2one(
        "res.partner", related="order_id.partner_shipping_id", readonly=True
    )
    ship_date = fields.Date(
        required=True, default=lambda self: fields.Date.today() + timedelta(days=1)
    )
    weight_source = fields.Selection(
        [("manual", "Manual Package Weight"), ("odoo", "Odoo Product Weights")],
        default="manual", required=True,
    )
    package_ids = fields.One2many(
        "motogene.fedex.quote.package", "wizard_id", string="Packages",
        default=lambda self: [(0, 0, {
            "weight": 1.0, "length": 40, "width": 30, "height": 25,
        })],
    )
    origin_country_id = fields.Many2one(
        "res.country", required=True,
        default=lambda self: self.env.ref("base.my").id,
        string="Country of Manufacture (all items)",
    )
    customs_description = fields.Char(
        required=True, default="Retail merchandise",
        help="Accurate goods description for this rate estimate; not a customs invoice.",
    )
    duties_payer = fields.Selection(
        [("SENDER", "Sender"), ("RECIPIENT", "Recipient")],
        required=True, default="SENDER",
    )

    def _assert_operator(self):
        if not (
            self.env.user.has_group("motogene_fedex_quotation.group_fedex_operator")
            or self.env.user.has_group("base.group_system")
        ):
            raise AccessError(_("FedEx rate quotations are Operations-only."))

    def action_fill_odoo_weight(self):
        self.ensure_one()
        self._assert_operator()
        products = self.order_id.order_line.filtered(
            lambda line: not line.display_type
            and line.product_id
            and line.product_id.type in ("consu", "product")
            and line.product_uom_qty > 0
        )
        if not products:
            raise UserError(_("No physical products were found in this order."))
        missing = products.filtered(lambda l: l.product_id.weight <= 0)
        if missing:
            raise UserError(
                _("Some product weights are missing: %s. Use verified manual weight.")
                % ", ".join(missing.mapped("product_id.display_name")[:5])
            )
        total = sum(l.product_id.weight * l.product_uom_qty for l in products)
        if len(self.package_ids) != 1:
            raise UserError(
                _("Odoo automatic weight is for one package only. "
                  "Enter each carton weight manually for multi-package shipping.")
            )
        self.package_ids.weight = total
        self.weight_source = "odoo"
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name, "res_id": self.id,
            "view_mode": "form", "target": "new",
        }

    def action_get_rates(self):
        self.ensure_one()
        self._assert_operator()
        order = self.order_id
        partner = order.partner_shipping_id
        if order.state not in ("draft", "sent"):
            raise UserError(_("Rate quotations are available only on draft Sales Orders."))
        if not partner.country_id or partner.country_id.code == "MY":
            raise UserError(_("This wizard is for international shipping addresses only."))
        if not partner.zip:
            raise UserError(_("Delivery address requires a postal code."))
        if not self.package_ids:
            raise UserError(_("Enter at least one package."))
        for box in self.package_ids:
            if min(box.weight, box.length, box.width, box.height) <= 0:
                raise UserError(_("Each package requires positive weight and dimensions."))
        items = order.order_line.filtered(
            lambda l: not l.display_type and l.product_id
            and l.product_uom_qty > 0 and l.product_id.type != "service"
        )
        if not items:
            raise UserError(
                _("Order must contain physical goods for international rating.")
            )
        if any(l.price_subtotal <= 0 for l in items):
            raise UserError(
                _("Zero-value goods require separate customs handling. "
                  "This first version only rates goods with a genuine positive value.")
            )
        if any(l.product_uom_qty != int(l.product_uom_qty) for l in items):
            raise UserError(_("This version requires whole-unit quantities."))
        if float_compare(sum(items.mapped("price_subtotal")), 0.0, precision_digits=2) <= 0:
            raise UserError(_("The order's goods value must be positive."))

        # Actual invoiced goods prices only. Do not substitute artificially low values.
        goods = []
        total_value = 0.0
        weight_total = sum(box.weight for box in self.package_ids)
        value_total = sum(items.mapped("price_subtotal"))
        for line in items:
            value = round(line.price_subtotal, 2)
            total_value += value
            # Rating estimate: apportions packed weight by actual goods value.
            # Before customs paperwork, collect exact per-commodity net weights.
            estimated_weight = round(weight_total * value / value_total, 3)
            goods.append({
                "description": line.product_id.display_name or self.customs_description,
                "countryOfManufacture": self.origin_country_id.code,
                "numberOfPieces": 1,
                "quantity": max(1, int(line.product_uom_qty)),
                "quantityUnits": "EA",
                "weight": {"units": "KG", "value": max(0.001, estimated_weight)},
                "unitPrice": {
                    "amount": round(value / max(1, int(line.product_uom_qty)), 2),
                    "currency": order.currency_id.name,
                },
                "customsValue": {
                    "amount": value,
                    "currency": order.currency_id.name,
                },
            })
        package_lines = [{
            "groupPackageCount": 1,
            "weight": {"units": "KG", "value": box.weight},
            "dimensions": {
                "length": box.length, "width": box.width,
                "height": box.height, "units": "CM",
            },
        } for box in self.package_ids]
        shipment = {
            "shipper": {
                "address": {
                    "postalCode": self.env.company.partner_id.zip or "",
                    "countryCode": "MY",
                },
            },
            "recipient": {
                "address": {
                    "postalCode": partner.zip,
                    "countryCode": partner.country_id.code,
                },
            },
            "pickupType": "DROPOFF_AT_FEDEX_LOCATION",
            "totalPackageCount": len(package_lines),
            "shipDateStamp": fields.Date.to_string(self.ship_date),
            "packagingType": "YOUR_PACKAGING",
            "rateRequestType": ["ACCOUNT"],
            "requestedPackageLineItems": package_lines,
            "customsClearanceDetail": {
                "dutiesPayment": {"paymentType": self.duties_payer},
                "customsValue": {
                    "amount": round(total_value, 2),
                    "currency": order.currency_id.name,
                },
                "commodities": goods,
            },
        }
        if not shipment["shipper"]["address"]["postalCode"]:
            raise UserError(_("Company sender postal code is required."))
        rates = self.env["motogene.fedex.api"].get_rates(shipment)
        if not rates:
            raise UserError(_("FedEx returned no account rates for this request."))

        Quote = self.env["motogene.fedex.quote"]
        for rate in rates:
            Quote.create({
                **rate,
                "order_id": order.id,
                "ship_date": self.ship_date,
                "destination_country_id": partner.country_id.id,
            })
        return {
            "type": "ir.actions.act_window",
            "name": _("FedEx Shipping Rates"),
            "res_model": "sale.order", "res_id": order.id,
            "view_mode": "form",
            "target": "current",
        }
