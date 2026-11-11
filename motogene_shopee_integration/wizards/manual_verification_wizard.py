"""Human review of recipient data lawfully visible in Shopee Seller Centre."""
import re

from odoo import fields, models, _
from odoo.exceptions import AccessError, UserError


def _canonical(value):
    return " ".join(re.sub(r"[^\w\s]", " ", str(value or "").casefold()).split())


class ShopeeManualVerificationWizard(models.TransientModel):
    _name = "motogene.shopee.manual.verify.wizard"
    _description = "Verify Shopee Recipient from Seller Centre AWB"

    sale_order_id = fields.Many2one("sale.order", required=True, readonly=True)
    seller_centre_order_sn = fields.Char(
        string="Order SN on Seller Centre", required=True,
        help="Type the exact Shopee Order SN to avoid matching the wrong AWB.",
    )
    awb_number = fields.Char(
        string="AWB / Tracking Number", required=True,
        help="Use the already-arranged AWB from Shopee Seller Centre.",
    )
    match_mode = fields.Selection([
        ("existing", "Use Existing Odoo Contact"),
        ("new", "Create New Odoo Contact"),
    ], required=True, default="existing")
    existing_partner_id = fields.Many2one(
        "res.partner", string="Existing Contact",
        domain="[('active','=',True)]",
    )
    recipient_name = fields.Char(string="Verified Recipient Name", required=True)
    recipient_phone = fields.Char(
        string="Recipient Phone (if available)",
        help="Leave blank if Seller Centre does not display the phone; never enter masked values.",
    )
    recipient_street = fields.Char(
        string="Full Delivery Address (from Seller Centre)", required=True,
    )
    recipient_zip = fields.Char(string="Postcode", required=True)
    recipient_city = fields.Char(string="City")
    source_confirmed = fields.Boolean(
        string="I checked this order, AWB, name and full address in Seller Centre",
        default=False,
    )

    def action_verify(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators can verify Shopee customers."))

        sale = self.sale_order_id.sudo()
        self.env.cr.execute(
            "SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [sale.id]
        )
        sale.invalidate_recordset()

        if (sale.state not in ("draft", "sent")
                or sale.motogene_shopee_channel != "shopee"
                or sale.motogene_shopee_manual_state != "pending"
                or not sale.motogene_shopee_preview_id):
            raise UserError(_("This Shopee quotation is not awaiting verification."))
        if not self.source_confirmed:
            raise UserError(_("Confirm that you checked the Seller Centre order and AWB."))
        if (self.seller_centre_order_sn or "").strip() != (
            sale.motogene_shopee_order_ref or ""
        ).strip():
            raise UserError(_("Seller Centre Order SN does not match this quotation."))

        awb = (self.awb_number or "").strip()
        name = (self.recipient_name or "").strip()
        street = (self.recipient_street or "").strip()
        postcode = (self.recipient_zip or "").strip()
        city = (self.recipient_city or "").strip()
        phone = (self.recipient_phone or "").strip()
        if (not all((awb, name, street, postcode))
                or any("*" in value for value in (awb, name, street, postcode, phone))):
            raise UserError(_(
                "AWB, recipient name, complete delivery address and postcode are required. "
                "Masked values are not accepted."
            ))
        if len(awb) > 80 or any(ch.isspace() for ch in awb):
            raise UserError(_("Check the AWB number: it must not contain spaces."))
        if not re.fullmatch(r"[0-9]{5}", postcode):
            raise UserError(_("Enter the verified five-digit Malaysian postcode."))
        another = self.env["sale.order"].sudo().search([
            ("motogene_shopee_manual_awb_no", "=", awb),
            ("id", "!=", sale.id),
        ], limit=1)
        if another:
            raise UserError(_(
                "This AWB is already linked to a different Sales Order. "
                "Check the Seller Centre Order SN and AWB."
            ))

        if sale.company_id != self.env.company:
            raise UserError(_("Switch to the company of this Shopee Sales Order."))

        Partner = self.env["res.partner"].sudo().with_company(sale.company_id)
        placeholder = self.env.ref(
            "motogene_shopee_integration.partner_shopee_pending_verification"
        )
        if self.match_mode == "existing":
            partner = self.existing_partner_id.sudo()
            if (not partner.exists() or not partner.active or partner == placeholder
                    or partner.company_id and partner.company_id != sale.company_id):
                raise UserError(_("Select a valid existing contact in the same company."))
            # No name-only matching. An exact normalised name + full address + postcode
            # match is necessary before reusing an existing recipient contact.
            saved_street = " ".join(
                value for value in (partner.street, partner.street2) if value
            )
            if (_canonical(partner.name) != _canonical(name)
                    or _canonical(saved_street) != _canonical(street)
                    or _canonical(partner.zip) != _canonical(postcode)
                    or partner.country_id.code != "MY"):
                raise UserError(_(
                    "Existing contact does not match the verified name, full address "
                    "and postcode. Do not reuse a customer by name alone. "
                    "Select a matching delivery contact or create a new one."
                ))
            known_phones = [v for v in (partner.phone, partner.mobile) if v]
            if phone and known_phones and not any(
                re.sub(r"\\D", "", v) == re.sub(r"\\D", "", phone)
                for v in known_phones
            ):
                raise UserError(_(
                    "The AWB phone does not match the existing contact. "
                    "Review manually; do not overwrite the original contact."
                ))
        elif self.match_mode == "new":
            country = self.env.ref("base.my", raise_if_not_found=False)
            if not country:
                raise UserError(_("Malaysia country record not found."))
            partner = Partner.create({
                "name": name,
                "phone": phone or False,
                "street": street,
                "zip": postcode,
                "city": city or False,
                "country_id": country.id,
                "company_id": sale.company_id.id,
                "type": "contact",
                "customer_rank": 1,
            })
        else:
            raise UserError(_("Choose Existing Contact or New Contact."))

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
        sale.message_post(body=_(
            "Shopee recipient manually verified from authorized Seller Centre "
            "order/AWB information by %s. No Shopee shipment API request was sent."
        ) % self.env.user.name)

        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id": sale.id,
            "view_mode": "form",
            "target": "current",
        }
