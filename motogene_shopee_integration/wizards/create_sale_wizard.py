from odoo import fields, models, _
from odoo.exceptions import UserError


class ShopeeCreateSaleWizard(models.TransientModel):
    _name = "motogene.shopee.create.sale.wizard"
    _description = "Create Draft SO from Shopee Preview (Staging Only)"

    preview_id = fields.Many2one(
        "motogene.shopee.import.order", required=True, readonly=True)
    partner_id = fields.Many2one(
        "res.partner", string="Customer (Test)", required=True,
        help="Select a test customer in staging. No customer is created from Shopee data.")
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
        readonly=True)

    def action_create_draft_so(self):
        self.ensure_one()
        preview = self.preview_id.sudo()
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only system administrators can create test quotations."))
        if preview.shop_id.environment != "sandbox":
            raise UserError(_("Phase 3A is limited to sandbox."))
        if preview.mapping_status != "matched" or not preview.line_ids:
            raise UserError(_("All Shopee items must be mapped before creating a quotation."))
        if preview.order_status != "READY_TO_SHIP":
            raise UserError(_("Phase 3A only allows READY_TO_SHIP test orders."))
        if self.env["ir.config_parameter"].sudo().get_param(
            "motogene_shopee.enable_test_so", default="0"
        ) != "1":
            raise UserError(_("Phase 3A test SO creation is disabled. Enable it explicitly on Odoo staging."))
        if self.company_id != self.env.company:
            raise UserError(_("Switch to the intended test company before creating the quotation."))
        Order = self.env["sale.order"].sudo().with_company(self.company_id)
        # Lock the source row, preventing concurrent creates from this preview.
        self.env.cr.execute(
            "SELECT id FROM motogene_shopee_import_order WHERE id = %s FOR UPDATE",
            [preview.id],
        )
        other = Order.search([("motogene_shopee_preview_id", "=", preview.id)], limit=1)
        if not other and "shopee_order_ref" in Order._fields:
            other = Order.search([("shopee_order_ref", "=", preview.name)], limit=1)
        if other:
            raise UserError(_("This Shopee Order SN is already linked to SO %s.") % other.name)
        if any(
            not line.product_id or line.quantity <= 0
            for line in preview.line_ids
        ):
            raise UserError(_("Invalid or unmapped item quantity."))
        # The native connector fields (shopee_shop_id, shopee_order_ref)
        # are deliberately NOT written here: they belong to Odoo's sale_shopee.
        lines = []
        for line in preview.line_ids:
            product = line.product_id.with_company(self.company_id)
            if not product.sale_ok:
                raise UserError(_("Product %s is not available for Sales.") % product.display_name)
            lines.append((0, 0, {
                "product_id": product.id,
                "name": product.display_name,
                "product_uom_qty": line.quantity,
                "product_uom": product.uom_id.id,
                "price_unit": line.shopee_discounted_price,
                "discount": 0.0,
            }))
        so = Order.create({
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "client_order_ref": preview.name,
            "origin": "MotoGene Shopee Sandbox: " + preview.name,
            "motogene_shopee_preview_id": preview.id,
            "order_line": lines,
        })
        # Explicitly enforce source transaction line price after SO creation.
        # No order confirmation, invoice, shipping carrier, or stock reservation.
        for so_line, source_line in zip(
            so.order_line.filtered(lambda l: not l.display_type),
            preview.line_ids,
        ):
            so_line.write({
                "price_unit": source_line.shopee_discounted_price,
                "discount": 0.0,
            })
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id": so.id,
            "view_mode": "form",
            "target": "current",
        }
