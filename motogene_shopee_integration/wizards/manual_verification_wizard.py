"""Simple, staff-controlled customer selection for Shopee draft quotations.

Staff review the genuine Seller Centre order/AWB themselves and choose an
existing Odoo delivery contact, or create a new one. The API's masked recipient
fields are never used to guess identity. This wizard never arranges shipment.
"""
import re

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class ShopeeManualVerificationWizard(models.TransientModel):
    _name = "motogene.shopee.manual.verify.wizard"
    _description = "Assign Shopee Customer and Seller Centre AWB"

    sale_order_id = fields.Many2one(
        "sale.order", required=True, readonly=True,
    )
    awb_number = fields.Char(
        string="Seller Centre AWB / Tracking Number", required=True,
        help="Enter the AWB already arranged in Shopee Seller Centre. "
             "Odoo will not arrange shipment again.",
    )
    match_mode = fields.Selection([
        ("existing", "Select Existing Odoo Contact"),
        ("new", "Create New Odoo Contact"),
    ], string="Customer", required=True, default="existing")
    existing_partner_id = fields.Many2one(
        "res.partner", string="Select Existing Contact",
        domain="[('active','=',True)]",
        help="Staff must compare this contact's actual delivery address and "
             "recipient name with Seller Centre before selecting it. "
             "Odoo does not automatically compare the addresses.",
    )
    recipient_name = fields.Char(string="New Contact Name")
    recipient_phone = fields.Char(string="Phone (if available)")
    recipient_street = fields.Char(string="New Contact Full Address")
    recipient_zip = fields.Char(string="Postcode")
    recipient_city = fields.Char(string="City")

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        sale_id = values.get("sale_order_id") or self.env.context.get(
            "default_sale_order_id"
        )
        if not sale_id:
            return values
        sale = self.env["sale.order"].sudo().browse(sale_id).exists()
        if not sale:
            return values
        if "awb_number" in field_names and sale.motogene_shopee_manual_awb_no:
            values["awb_number"] = sale.motogene_shopee_manual_awb_no
        if (sale.motogene_shopee_manual_state == "verified"
                and sale.partner_id
                and "existing_partner_id" in field_names):
            values["existing_partner_id"] = sale.partner_id.id
        return values

    def action_verify(self):
        """Assign customer + AWB, without strict text matching against Seller Centre."""
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators can assign Shopee customers."))

        sale = self.sale_order_id.sudo()
        self.env.cr.execute(
            "SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [sale.id]
        )
        sale.invalidate_recordset()
        if (sale.state not in ("draft", "sent")
                or sale.motogene_shopee_channel != "shopee"
                or sale.motogene_shopee_manual_state not in ("pending", "verified")
                or not sale.motogene_shopee_preview_id):
            raise UserError(_("This is not a Shopee draft awaiting customer assignment."))
        if (sale.motogene_shopee_preview_id.order_status
                in ("CANCELLED", "UNPAID")):
            raise UserError(_(
                "Shopee order is cancelled or unpaid. Check Seller Centre first."
            ))
        if sale.company_id != self.env.company:
            raise UserError(_("Switch to the company of this Shopee Sales Order."))

        awb = (self.awb_number or "").strip()
        if (not awb or len(awb) > 80 or any(ch.isspace() for ch in awb)
                or "*" in awb):
            raise UserError(_(
                "Enter a valid unmasked AWB number from the shipment "
                "already arranged in Seller Centre."
            ))
        another = self.env["sale.order"].sudo().search([
            ("motogene_shopee_manual_awb_no", "=", awb),
            ("id", "!=", sale.id),
        ], limit=1)
        if another:
            raise UserError(_(
                "This AWB is already linked to another Sales Order. "
                "Check the tracking number and Shopee order before continuing."
            ))

        placeholder = self.env.ref(
            "motogene_shopee_integration.partner_shopee_pending_verification"
        )
        Partner = self.env["res.partner"].sudo().with_company(sale.company_id)

        if self.match_mode == "existing":
            partner = self.existing_partner_id.sudo().exists()
            if (not partner or not partner.active or partner == placeholder
                    or (partner.company_id
                        and partner.company_id != sale.company_id)):
                raise UserError(_("Select an active, appropriate existing Odoo contact."))
            # Human review replaces duplicate data entry. Still reject contacts
            # that cannot be used as real delivery recipients.
            if (not partner.name or not partner.street or not partner.zip
                    or partner.country_id.code != "MY"
                    or any("*" in (value or "") for value in (
                        partner.name, partner.street, partner.zip,
                    ))):
                raise UserError(_(
                    "The selected contact has an incomplete or masked Malaysian "
                    "shipping address. Update the contact from an authorized "
                    "source or choose Create New Contact."
                ))
        elif self.match_mode == "new":
            name = (self.recipient_name or "").strip()
            street = (self.recipient_street or "").strip()
            zipcode = (self.recipient_zip or "").strip()
            city = (self.recipient_city or "").strip()
            phone = (self.recipient_phone or "").strip()
            if (not name or not street or not zipcode
                    or any("*" in value for value in (
                        name, street, zipcode, city, phone,
                    ))):
                raise UserError(_(
                    "For a new contact, enter the real recipient name, full "
                    "address and postcode. Masked values are not allowed."
                ))
            if not re.fullmatch(r"[0-9]{5}", zipcode):
                raise UserError(_("Enter a five-digit Malaysian postcode."))
            country = self.env.ref("base.my", raise_if_not_found=False)
            if not country:
                raise UserError(_("Malaysia country record is missing."))
            partner = Partner.create({
                "name": name,
                "phone": phone or False,
                "street": street,
                "zip": zipcode,
                "city": city or False,
                "country_id": country.id,
                "company_id": sale.company_id.id,
                "type": "contact",
                "customer_rank": 1,
            })
        else:
            raise UserError(_("Select Existing Contact or Create New Contact."))

        sale.write({
            "partner_id": partner.id,
            "partner_invoice_id": partner.id,
            "partner_shipping_id": partner.id,
            "motogene_shopee_manual_awb_no": awb,
            "motogene_shopee_manual_state": "verified",
            "motogene_shopee_manual_verified_by": self.env.user.id,
            "motogene_shopee_manual_verified_at": fields.Datetime.now(),
        })
        sale.write({
            "motogene_shopee_manual_identity_hash":
                sale._motogene_shopee_manual_identity_hash(),
        })
        # Log the review action, not the recipient's PII.
        sale.message_post(body=_(
            "Shopee customer and Seller Centre AWB assigned by %s after "
            "staff review. No Shopee shipment request was sent."
        ) % self.env.user.name)
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id": sale.id,
            "view_mode": "form",
            "target": "current",
        }
