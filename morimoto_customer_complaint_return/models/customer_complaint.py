# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CustomerComplaint(models.Model):
    _name = "customer.complaint"
    _description = "Customer Complaint / Product Return"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_reported desc, id desc"

    # ---------------------------------------------------------
    # BASIC INFO
    # ---------------------------------------------------------
    name = fields.Char(
        string="Complaint Number",
        required=True,
        copy=False,
        readonly=True,
        default="New",
        tracking=True,
    )

    date_reported = fields.Date(
        string="Complaint Date",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )

    channel = fields.Selection(
        [
            ("phone", "Phone Call"),
            ("whatsapp", "WhatsApp"),
            ("email", "Email"),
            ("shopee", "Shopee"),
            ("tiktok", "TikTok Shop"),
            ("marketplace", "Other Marketplace"),
            ("walk_in", "Walk-in"),
            ("other", "Other"),
        ],
        string="Channel",
        default="other",
        tracking=True,
    )

    complaint_type = fields.Selection(
        [
            ("product_quality", "Product Quality"),
            ("delivery_issue", "Delivery / Shipping"),
            ("billing_issue", "Billing / Payment"),
            ("service", "Customer Service"),
            ("return_request", "Product Return Only"),
            ("other", "Other"),
        ],
        string="Complaint Type",
        tracking=True,
    )

    partner_id = fields.Many2one(
        "res.partner",
        string="Customer",
        required=True,
        tracking=True,
        domain=[("customer_rank", ">", 0)],
    )

    phone = fields.Char(
        string="Phone",
        related="partner_id.phone",
        readonly=True,
    )

    email = fields.Char(
        string="Email",
        related="partner_id.email",
        readonly=True,
    )

    # ---------------------------------------------------------
    # RELATED DOCUMENTS
    # ---------------------------------------------------------
    sale_order_id = fields.Many2one(
        "sale.order",
        string="Sales Order",
        domain="[('partner_id', '=', partner_id)]",
        tracking=True,
    )

    invoice_id = fields.Many2one(
        "account.move",
        string="Invoice",
        domain="[('move_type', '=', 'out_invoice'), ('partner_id', '=', partner_id)]",
        tracking=True,
    )

    picking_id = fields.Many2one(
        "stock.picking",
        string="Delivery Order",
        domain="[('partner_id', '=', partner_id)]",
        tracking=True,
    )

    # ---------------------------------------------------------
    # DESCRIPTION FIELDS
    # ---------------------------------------------------------
    description = fields.Text(
        string="Complaint Description",
        help="Details of customer's complaint.",
        tracking=True,
    )

    internal_note = fields.Text(
        string="Internal Notes",
        help="Internal notes for staff only.",
    )

    resolution = fields.Text(
        string="Resolution / Follow-up",
        help="How the complaint was handled and final decision.",
    )

    # ---------------------------------------------------------
    # RETURN SECTION
    # ---------------------------------------------------------
    is_return_involved = fields.Boolean(
        string="Product Return Involved",
        help="Tick if this complaint involves physical product return.",
    )

    return_line_ids = fields.One2many(
        "customer.complaint.line",
        "complaint_id",
        string="Returned Products",
    )

    return_total_qty = fields.Float(
        string="Total Returned Quantity",
        compute="_compute_return_totals",
        store=True,
    )

    return_line_count = fields.Integer(
        string="Number of Return Lines",
        compute="_compute_return_totals",
        store=True,
    )

    # ---------------------------------------------------------
    # STATUS & RESPONSIBLE
    # ---------------------------------------------------------
    state = fields.Selection(
        [
            ("new", "New"),
            ("in_progress", "In Progress"),
            ("waiting_return", "Waiting Return Stock"),
            ("closed", "Closed"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        default="new",
        tracking=True,
    )

    responsible_id = fields.Many2one(
        "res.users",
        string="Responsible",
        default=lambda self: self.env.user,
        tracking=True,
    )

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )

    # ---------------------------------------------------------
    # TOTAL RETURN COMPUTE
    # ---------------------------------------------------------
    @api.depends("return_line_ids.quantity_returned")
    def _compute_return_totals(self):
        for rec in self:
            total = sum(rec.return_line_ids.mapped("quantity_returned"))
            rec.return_total_qty = total
            rec.return_line_count = len(rec.return_line_ids)

    # ---------------------------------------------------------
    # UNIQUE COMPLAINT NUMBER
    # ---------------------------------------------------------
    _sql_constraints = [
        ("name_uniq", "unique(name)", "Complaint number must be unique!"),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("customer.complaint") or "New"
                )
        return super().create(vals_list)

    # ---------------------------------------------------------
    # ONCHANGE: SALES ORDER -> CUSTOMER, INVOICE, DO
    # ---------------------------------------------------------
    @api.onchange("sale_order_id")
    def _onchange_sale_order_id(self):
        """Auto-fill customer, invoice, delivery order when SO selected."""
        for rec in self:
            if rec.sale_order_id:
                so = rec.sale_order_id

                # Auto customer
                rec.partner_id = so.partner_id

                # Auto invoice (first non-cancel customer invoice)
                invoice = so.invoice_ids.filtered(
                    lambda m: m.move_type == "out_invoice" and m.state != "cancel"
                )[:1]
                rec.invoice_id = invoice.id if invoice else False

                # Auto delivery order (first outgoing & not cancelled)
                picking = so.picking_ids.filtered(
                    lambda p: p.picking_type_code == "outgoing"
                    and p.state != "cancel"
                )[:1]
                rec.picking_id = picking.id if picking else False
            else:
                rec.invoice_id = False
                rec.picking_id = False

    # ---------------------------------------------------------
    # ONCHANGE: DELIVERY ORDER -> AUTO LOAD PRODUCT LINES
    # ---------------------------------------------------------
    @api.onchange("picking_id")
    def _onchange_picking_id_load_lines(self):
        """When a Delivery Order is chosen, load its products into Returned Products tab."""
        for rec in self:
            if not rec.picking_id:
                # Clear lines if DO cleared
                rec.return_line_ids = [(5, 0, 0)]
                continue

            lines_vals = []
            for move in rec.picking_id.move_ids_without_package:
                # Skip services if any
                if move.product_id.type == "service":
                    continue

                # Use delivered qty (quantity if validated, else planned qty)
                qty_delivered = move.quantity or move.product_uom_qty

                lines_vals.append(
                    (
                        0,
                        0,
                        {
                            "product_id": move.product_id.id,
                            "quantity_purchased": qty_delivered,
                            "uom_id": move.product_uom.id,
                            # quantity_returned staff akan isi sendiri
                        },
                    )
                )

            # (5, 0, 0) clear existing lines, then add new ones
            rec.return_line_ids = [(5, 0, 0)] + lines_vals

    # ---------------------------------------------------------
    # STATE BUTTONS
    # ---------------------------------------------------------
    def action_set_new(self):
        self.write({"state": "new"})

    def action_set_in_progress(self):
        self.write({"state": "in_progress"})

    def action_set_waiting_return(self):
        self.write({"state": "waiting_return"})

    def action_set_closed(self):
        self.write({"state": "closed"})

    def action_set_cancelled(self):
        self.write({"state": "cancelled"})


class CustomerComplaintLine(models.Model):
    _name = "customer.complaint.line"
    _description = "Customer Complaint Product Line"

    complaint_id = fields.Many2one(
        "customer.complaint",
        string="Complaint",
        required=True,
        ondelete="cascade",
    )

    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
    )

    lot_id = fields.Many2one(
        "stock.lot",
        string="Lot/Batch",
        domain="[('product_id', '=', product_id)]",
    )

    quantity_purchased = fields.Float(
        string="Purchased Qty",
        digits="Product Unit of Measure",
    )

    quantity_returned = fields.Float(
        string="Returned Qty",
        digits="Product Unit of Measure",
    )

    uom_id = fields.Many2one(
        "uom.uom",
        string="Unit of Measure",
        related="product_id.uom_id",
        readonly=False,
    )

    reason = fields.Selection(
        [
            ("damage", "Damaged"),
            ("defect", "Defective"),
            ("expired", "Expired / Near Expiry"),
            ("wrong_item", "Wrong Item"),
            ("packing_issue", "Packing Issue"),
            ("customer_change_mind", "Customer Change of Mind"),
            ("other", "Other"),
        ],
        string="Return Reason",
    )

    remark = fields.Char(string="Remark")

    sale_line_id = fields.Many2one(
        "sale.order.line",
        string="Sales Line",
        domain="[('order_id', '=', parent.sale_order_id)]",
    )
