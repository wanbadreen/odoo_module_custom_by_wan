"""Explicit, review-gated fallback for Shopee orders with masked API recipient PII.

This workflow never calls the Shopee shipment-mutating API. The AWB and recipient
information must be obtained by authorized staff from Seller Centre.
"""
import hashlib

from odoo import fields, models, _
from odoo.exceptions import AccessError, UserError


class ShopeeManualSaleOrder(models.Model):
    _inherit = "sale.order"

    motogene_shopee_manual_state = fields.Selection(
        [
            ("pending", "Pending Customer Verification"),
            ("verified", "Manually Verified"),
        ],
        string="Shopee Manual Verification",
        readonly=True,
        copy=False,
        tracking=True,
    )
    motogene_shopee_manual_awb_no = fields.Char(
        string="Shopee Seller Centre AWB",
        readonly=True,
        copy=False,
    )
    motogene_shopee_manual_verified_by = fields.Many2one(
        "res.users", string="Verified By", readonly=True, copy=False,
    )
    motogene_shopee_manual_verified_at = fields.Datetime(
        string="Verified At", readonly=True, copy=False,
    )
    motogene_shopee_manual_identity_hash = fields.Char(
        string="Verified Recipient Fingerprint",
        readonly=True, copy=False, groups="base.group_system",
    )

    def _motogene_shopee_manual_identity_hash(self):
        """Invalidate prior verification if recipient/contact/AWB changes."""
        self.ensure_one()
        recipient = self.partner_shipping_id
        parts = [
            str(self.partner_id.id),
            str(recipient.id),
            recipient.name or "",
            recipient.phone or "",
            recipient.street or "",
            recipient.street2 or "",
            recipient.zip or "",
            recipient.city or "",
            recipient.country_id.code or "",
            (self.motogene_shopee_manual_awb_no or "").strip(),
        ]
        canonical = "|".join(
            " ".join(str(value).casefold().split()) for value in parts
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def action_open_motogene_shopee_manual_verification(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators may verify Shopee recipients."))
        if (self.motogene_shopee_channel != "shopee"
                or self.motogene_shopee_manual_state != "pending"
                or self.state not in ("draft", "sent")):
            raise UserError(_("Only pending Shopee draft quotations can be verified."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Verify Shopee Customer from Seller Centre"),
            "res_model": "motogene.shopee.manual.verify.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_sale_order_id": self.id},
        }

    def action_confirm(self):
        pending_partner = self.env.ref(
            "motogene_shopee_integration.partner_shopee_pending_verification",
            raise_if_not_found=False,
        )
        for order in self:
            if (pending_partner and (order.partner_id == pending_partner
                                     or order.partner_shipping_id == pending_partner)):
                raise UserError(_(
                    "Shopee customer is not verified. Select the correct recipient "
                    "before confirming this Sales Order."
                ))
            if order.motogene_shopee_manual_state == "pending":
                raise UserError(_(
                    "Shopee recipient verification is pending. Use Verify Shopee "
                    "Customer before confirming the Sales Order."
                ))
            if order.motogene_shopee_manual_state == "verified":
                if order.motogene_shopee_channel != "shopee":
                    raise UserError(_("Verified Shopee order channel was changed."))
                partner = order.partner_shipping_id
                if (order.partner_id != partner or not partner.name
                        or not partner.street or not partner.zip
                        or partner.country_id.code != "MY"):
                    raise UserError(_("Verified Shopee recipient address is incomplete."))
                secure_order = order.sudo()
                if (not order.motogene_shopee_manual_awb_no
                        or not secure_order.motogene_shopee_manual_identity_hash
                        or secure_order.motogene_shopee_manual_identity_hash
                        != secure_order._motogene_shopee_manual_identity_hash()):
                    raise UserError(_(
                        "Shopee recipient, address, phone or AWB changed since verification. "
                        "Please re-verify before confirming."
                    ))
        result = super().action_confirm()

        # Save the Seller Centre AWB on newly generated DOs without calling ship_order.
        # Raising on an AWB conflict rolls back the complete SO confirmation.
        for order in self.filtered(
            lambda so: so.motogene_shopee_channel == "shopee"
            and so.motogene_shopee_manual_state == "verified"
        ):
            tracking = (order.motogene_shopee_manual_awb_no or "").strip()
            for picking in order.picking_ids.filtered(
                lambda p: p.picking_type_code == "outgoing" and p.state != "cancel"
            ):
                if "mori_awb_no" not in picking._fields:
                    raise UserError(_(
                        "Operations AWB field mori_awb_no is missing on the Delivery Order."
                    ))
                existing = (picking.mori_awb_no or "").strip()
                saved = (picking.motogene_shopee_tracking_number or "").strip()
                if ((existing and existing != tracking)
                        or (saved and saved != tracking)):
                    raise UserError(_(
                        "Existing Delivery Order AWB conflicts with Seller Centre AWB. "
                        "No tracking number was overwritten."
                    ))
                picking.sudo().write({
                    "mori_awb_no": tracking,
                    "motogene_shopee_tracking_number": tracking,
                    "motogene_shopee_shipment_state": "external",
                })
        return result


class ShopeeManualStockPicking(models.Model):
    _inherit = "stock.picking"

    motogene_shopee_shipment_state = fields.Selection(
        selection_add=[("external", "Arranged in Seller Centre")],
        ondelete={"external": "set default"},
    )

    def _motogene_shopee_external_status(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators may manage Shopee AWBs."))
        return self._motogene_shopee_notify(
            _("Shipment already arranged in Seller Centre"),
            _("This order was arranged outside Odoo. Do not send another shipment "
              "request. Print the AWB from Seller Centre or attach it to this DO."),
            "warning",
        )

    def action_motogene_shopee_prepare(self):
        self.ensure_one()
        if self.motogene_shopee_shipment_state == "external":
            return self._motogene_shopee_external_status()
        return super().action_motogene_shopee_prepare()

    def action_motogene_shopee_arrange_shipment(self):
        self.ensure_one()
        if self.motogene_shopee_shipment_state == "external":
            return self._motogene_shopee_external_status()
        return super().action_motogene_shopee_arrange_shipment()

    def action_motogene_shopee_check_progress(self):
        self.ensure_one()
        if self.motogene_shopee_shipment_state == "external":
            return self._motogene_shopee_external_status()
        return super().action_motogene_shopee_check_progress()
