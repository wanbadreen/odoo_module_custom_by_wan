from odoo import fields, models, _
from odoo.exceptions import UserError


class ShopeeCreateSaleWizard(models.TransientModel):
    _name = "motogene.shopee.create.sale.wizard"
    _description = "Create Draft SO from Shopee Preview (Staging Only)"

    preview_id = fields.Many2one(
        "motogene.shopee.import.order", required=True, readonly=True)
    partner_id = fields.Many2one(
        "res.partner", string="Legacy Test Customer (Ignored)",
        help="Shopee buyer and shipping contact are resolved automatically from verified API identity.")
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
        readonly=True)

    def action_create_draft_so(self):
        self.ensure_one()
        preview = self.preview_id.sudo()
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only system administrators can create test quotations."))
        preview.shop_id._check_environment_access()
        if preview.shop_id.environment == "production" and (
            self.env["ir.config_parameter"].sudo().get_param(
                "motogene_shopee.enable_production_so_creation", "0"
            ) != "1"
        ):
            raise UserError(_("Production SO creation is locked pending mapping verification."))
        if preview.mapping_status != "matched" or not preview.line_ids:
            raise UserError(_("All Shopee items must be mapped before creating a quotation."))
        if preview.order_status != "READY_TO_SHIP":
            raise UserError(_("Phase 3A only allows READY_TO_SHIP test orders."))
        if preview.shop_id.environment == "sandbox" and (
            self.env["ir.config_parameter"].sudo().get_param(
                "motogene_shopee.enable_test_so", "0"
            ) != "1"
        ):
            raise UserError(_("Sandbox SO creation is disabled. Enable the staging test setting."))
        if self.company_id != self.env.company:
            raise UserError(_("Switch to the intended test company before creating the quotation."))
        Order = self.env["sale.order"].sudo().with_company(self.company_id)
        # Lock the source row, preventing concurrent creates from this preview.
        self.env.cr.execute(
            "SELECT id FROM motogene_shopee_import_order WHERE id = %s FOR UPDATE",
            [preview.id],
        )
        other = Order.search([
            "|",
            ("motogene_shopee_preview_id", "=", preview.id),
            ("motogene_shopee_order_ref", "=", preview.name),
        ], limit=1)
        if other:
            raise UserError(_("This Shopee Order SN is already linked to SO %s.") % other.name)
        if any(
            not line.product_id or line.quantity <= 0 or line.quantity_multiplier < 1
            for line in preview.line_ids
        ):
            raise UserError(_("Invalid or unmapped item quantity."))
        # Independent fields only; official sale_shopee is not a dependency.
        lines = []
        for line in preview.line_ids:
            product = line.product_id.with_company(self.company_id)
            if not product.sale_ok:
                raise UserError(_("Product %s is not available for Sales.") % product.display_name)
            multiplier = line.quantity_multiplier
            lines.append((0, 0, {
                "product_id": product.id,
                "name": product.display_name,
                "product_uom_qty": line.quantity * multiplier,
                "product_uom": product.uom_id.id,
                "price_unit": line.shopee_discounted_price / multiplier,
                "discount": 0.0,
            }))
        # Odoo Studio selection key includes a trailing space: 'SHOPEE '.
        order_type_field = Order._fields.get("x_studio_order_type")
        allowed = dict(Order.fields_get(
            ["x_studio_order_type"], attributes=["selection"]
        )["x_studio_order_type"].get("selection") or []) if order_type_field else {}
        if "SHOPEE " not in allowed:
            raise UserError(_(
                "Shopee Delivery Type ('SHOPEE ' with trailing space) is missing. "
                "Check the Odoo Studio selection before testing."
            ))
        if preview.escrow_status == "available":
            if preview.currency and preview.currency != self.company_id.currency_id.name:
                raise UserError(_("Shopee and company currencies differ; review income before importing."))

        customer, shipping = preview._motogene_shopee_resolve_customer_and_shipping(
            self.company_id
        )
        # Operations policy: AWB recipient is the visible SO customer.
        # Username remains reserved for invoice Payment Reference.
        if customer.id != shipping.id or customer.name != preview.recipient_name:
            raise UserError(_("Shopee recipient mapping is inconsistent; quotation not created."))
        so = Order.create({
            "partner_id": customer.id,
            "partner_invoice_id": customer.id,
            "partner_shipping_id": shipping.id,
            "company_id": self.company_id.id,
            "client_order_ref": preview.name,
            "motogene_shopee_order_ref": preview.name,
            "motogene_shopee_channel": "shopee",
            "x_studio_order_type": "SHOPEE ",
            "origin": "MotoGene Shopee %s: %s" % (
                preview.shop_id.environment.title(), preview.name
            ),
            "motogene_shopee_preview_id": preview.id,
            "order_line": lines,
        })
        # Only write an available Shopee escrow amount to the existing Studio field.
        if (preview.escrow_status == "available"
                and "x_studio_estimated_income_shopee_order_only" in Order._fields):
            so.write({
                "x_studio_estimated_income_shopee_order_only": preview.escrow_amount
            })
        # Explicitly enforce source transaction line price after SO creation.
        # No order confirmation, invoice, shipping carrier, or stock reservation.
        for so_line, source_line in zip(
            so.order_line.filtered(lambda l: not l.display_type),
            preview.line_ids,
        ):
            so_line.write({
                "price_unit": source_line.shopee_discounted_price / source_line.quantity_multiplier,
                "discount": 0.0,
            })
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id": so.id,
            "view_mode": "form",
            "target": "current",
        }
