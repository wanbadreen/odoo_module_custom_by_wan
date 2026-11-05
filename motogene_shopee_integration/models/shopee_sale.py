from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    motogene_shopee_channel = fields.Selection(
        [("shopee", "Shopee")], string="MotoGene Sales Channel",
        readonly=True, copy=False, index=True,
    )

    motogene_shopee_order_ref = fields.Char(
        string="MotoGene Shopee Order SN",
        copy=False, readonly=True, index=True,
    )

    motogene_shopee_preview_id = fields.Many2one(
        "motogene.shopee.import.order",
        string="MotoGene Shopee Preview",
        copy=False, readonly=True, index=True,
    )
    _sql_constraints = [
        (
            "motogene_shopee_ref_unique",
            "unique(motogene_shopee_order_ref)",
            "A MotoGene Shopee Order SN can only be linked once.",
        ),
        (
            "motogene_shopee_preview_unique",
            "unique(motogene_shopee_preview_id)",
            "A Shopee order preview can be linked to only one Sales Order.",
        ),
    ]

    def _prepare_invoice(self):
        """Use Shopee username and order SN as reference on new draft invoices."""
        vals = super()._prepare_invoice()
        self.ensure_one()
        if (self.motogene_shopee_channel != "shopee"
                or not self.motogene_shopee_preview_id
                or self.motogene_shopee_preview_id.shop_id.environment not in ("sandbox", "production")):
            return vals
        # Keep any reference another workflow explicitly prepared.
        if vals.get("payment_reference"):
            return vals
        preview = self.motogene_shopee_preview_id.sudo()
        order_sn = (self.motogene_shopee_order_ref or preview.name or "").strip()
        if not order_sn:
            return vals
        username = (preview.buyer_username or "").strip()
        # Never propagate a masked username or reserved delimiters.
        if not username or "*" in username:
            vals["payment_reference"] = order_sn[:255]
        else:
            safe_username = " ".join(username.replace("|", " ").split())[:128]
            vals["payment_reference"] = (safe_username + " | " + order_sn)[:255]
        return vals
