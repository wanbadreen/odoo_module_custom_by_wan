import hashlib

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ShopeeCustomerMapping(models.Model):
    _name = "motogene.shopee.customer.mapping"
    _description = "Shopee Buyer to Odoo Customer Mapping"
    _rec_name = "buyer_user_id"

    shop_id = fields.Many2one("motogene.shopee.shop", required=True, ondelete="cascade", index=True)
    buyer_user_id = fields.Char(required=True, index=True, readonly=True)
    partner_id = fields.Many2one("res.partner", required=True, ondelete="restrict", readonly=True)
    _sql_constraints = [
        ("unique_shopee_buyer", "unique(shop_id, buyer_user_id)",
         "This Shopee buyer is already linked to an Odoo customer."),
    ]


class ShopeeRecipientMapping(models.Model):
    _name = "motogene.shopee.recipient.mapping"
    _description = "Shopee Customer Delivery Address Mapping"
    _rec_name = "address_fingerprint"

    customer_mapping_id = fields.Many2one(
        "motogene.shopee.customer.mapping", required=True, ondelete="cascade", index=True,
    )
    address_fingerprint = fields.Char(required=True, index=True, readonly=True)
    partner_id = fields.Many2one("res.partner", required=True, ondelete="restrict", readonly=True)
    _sql_constraints = [
        ("unique_shopee_recipient", "unique(customer_mapping_id, address_fingerprint)",
         "This buyer's shipping address was mapped already."),
    ]


class ShopeeOrderRecipient(models.Model):
    _inherit = "motogene.shopee.import.order"

    def _motogene_shopee_resolve_customer_and_shipping(self, company):
        """Resolve one buyer and delivery address, without searching by fuzzy name."""
        self.ensure_one()
        if self.shop_id.environment != "sandbox" or not self.recipient_ready:
            raise UserError(_("Shopee recipient details unavailable. Do not create a SO with a placeholder address."))
        buyer_id = (self.buyer_user_id or "").strip()
        if not buyer_id or "*" in buyer_id:
            raise UserError(_("Shopee buyer_user_id missing or masked; manual review required."))
        if self.recipient_region != "MY":
            raise UserError(_("Only Malaysia sandbox shipping addresses are supported."))
        address = (self.recipient_full_address or "").strip()
        zipcode = (self.recipient_zipcode or "").strip()
        name = (self.recipient_name or "").strip()
        if not all((address, zipcode, name)):
            raise UserError(_("Missing Shopee recipient name, address or postal code."))
        M = self.env["motogene.shopee.customer.mapping"].sudo()
        A = self.env["motogene.shopee.recipient.mapping"].sudo()
        P = self.env["res.partner"].sudo().with_company(company)
        # Parent uses Shopee buyer ID as stable key. No fuzzy linking to unrelated contacts.
        self.env.cr.execute(
            "SELECT id FROM motogene_shopee_import_order WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.env.cr.execute(
            "SELECT id FROM motogene_shopee_shop WHERE id = %s FOR UPDATE",
            [self.shop_id.id],
        )
        buyer = M.search([
            ("shop_id", "=", self.shop_id.id),
            ("buyer_user_id", "=", buyer_id),
        ], limit=1)
        if not buyer:
            parent = P.create({
                "name": "Shopee Buyer %s" % buyer_id,
                "company_id": company.id,
                "customer_rank": 1,
            })
            buyer = M.create({
                "shop_id": self.shop_id.id,
                "buyer_user_id": buyer_id,
                "partner_id": parent.id,
            })
        elif buyer.partner_id.company_id and buyer.partner_id.company_id != company:
            raise UserError(_("Shopee buyer mapping belongs to a different company."))

        parts = [name, self.recipient_phone or "", address, zipcode,
                 self.recipient_city or "", self.recipient_state or "", "MY"]
        canonical = "|".join(" ".join(str(v).casefold().split()) for v in parts)
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        shipping = A.search([
            ("customer_mapping_id", "=", buyer.id),
            ("address_fingerprint", "=", fingerprint),
        ], limit=1)
        if not shipping:
            # Keep Shopee's full address verbatim rather than reconstructing it
            # into street/state fields and potentially losing components.
            country = self.env.ref("base.my", raise_if_not_found=False)
            if not country:
                raise UserError(_("Malaysia country record missing."))
            contact = P.create({
                "name": name,
                "type": "delivery",
                "parent_id": buyer.partner_id.id,
                "company_id": company.id,
                "phone": self.recipient_phone or False,
                "street": address,
                "zip": zipcode,
                "city": self.recipient_city or False,
                "country_id": country.id,
            })
            shipping = A.create({
                "customer_mapping_id": buyer.id,
                "address_fingerprint": fingerprint,
                "partner_id": contact.id,
            })
        return buyer.partner_id, shipping.partner_id
