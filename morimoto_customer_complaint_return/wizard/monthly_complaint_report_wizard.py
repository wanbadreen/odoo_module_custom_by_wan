# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.tools import format_date
import io
import base64
import xlsxwriter


class MonthlyComplaintReportWizard(models.TransientModel):
    _name = "monthly.complaint.report.wizard"
    _description = "Monthly Complaint Report Wizard"

    # -----------------------------------
    # FILTER FIELDS
    # -----------------------------------
    date_from = fields.Date(string="Date From", required=True)
    date_to = fields.Date(string="Date To", required=True)

    recipient_email = fields.Char(
        string="Recipient Email",
        required=True,
        default=lambda self: self.env.user.email or "info@morimotoformulas.com",
    )

    department_ids = fields.Many2many(
        "hr.department",
        string="Report from Department",
    )

    # guna selection yang sama dari customer.complaint
    complaint_type = fields.Selection(
        selection=lambda self: self.env["customer.complaint"]
        ._fields["complaint_type"]
        .selection,
        string="Complaint Type",
    )

    # sub-issue ikut type (ambil selection dari model complaint)
    product_quality_issue = fields.Selection(
        selection=lambda self: self.env["customer.complaint"]
        ._fields["x_studio_product_quality_issue"]
        .selection,
        string="Product Quality Issue",
    )

    deliveryshipping_issue = fields.Selection(
        selection=lambda self: self.env["customer.complaint"]
        ._fields["x_studio_deliveryshipping_issue"]
        .selection,
        string="Delivery / Shipping Issue",
    )

    billingpayment_issue = fields.Selection(
        selection=lambda self: self.env["customer.complaint"]
        ._fields["x_studio_billingpayment_issue"]
        .selection,
        string="Billing / Payment Issue",
    )

    customer_service_issue = fields.Selection(
        selection=lambda self: self.env["customer.complaint"]
        ._fields["x_studio_customer_service_issue"]
        .selection,
        string="Customer Service Issue",
    )

    is_product_return_involved = fields.Boolean(
        string="Product Return Involved",
        help="Filter only complaints with Product Return Involved = True",
    )

    channel_tag_ids = fields.Many2many(
        "crm.tag",
        string="Channel Tags",
        help="Filter complaints that have ANY of these channel tags.",
    )

    state = fields.Selection(
        [
            ("new", "New"),
            ("in_progress", "In Progress"),
            ("waiting_return", "Waiting Return Stock"),
            ("closed", "Closed"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
    )

    # -----------------------------------
    # DOMAIN BUILDER
    # -----------------------------------
    def _build_domain(self, only_dates=False):
        """Bina domain untuk search customer.complaint"""
        self.ensure_one()

        domain = [
            ("date_reported", ">=", self.date_from),
            ("date_reported", "<=", self.date_to),
        ]

        if only_dates:
            return domain

        # Department
        if self.department_ids:
            domain.append(
                ("x_studio_report_from_department", "in", self.department_ids.ids)
            )

        # Complaint Type + sub-issue
        if self.complaint_type:
            domain.append(("complaint_type", "=", self.complaint_type))

            if self.complaint_type == "product_quality" and self.product_quality_issue:
                domain.append(
                    (
                        "x_studio_product_quality_issue",
                        "=",
                        self.product_quality_issue,
                    )
                )
            elif (
                self.complaint_type == "delivery_issue"
                and self.deliveryshipping_issue
            ):
                domain.append(
                    (
                        "x_studio_deliveryshipping_issue",
                        "=",
                        self.deliveryshipping_issue,
                    )
                )
            elif self.complaint_type == "billing_issue" and self.billingpayment_issue:
                domain.append(
                    (
                        "x_studio_billingpayment_issue",
                        "=",
                        self.billingpayment_issue,
                    )
                )
            elif self.complaint_type == "service" and self.customer_service_issue:
                domain.append(
                    (
                        "x_studio_customer_service_issue",
                        "=",
                        self.customer_service_issue,
                    )
                )
            # complaint_type == 'return_request' tak ada sub-field khas, guna type sahaja

        # Product Return Involved (boolean custom)
        if self.is_product_return_involved:
            domain.append(("x_studio_is_product_return_involved", "=", True))

        # Channel tags (many2many)
        if self.channel_tag_ids:
            domain.append(("x_studio_channel", "in", self.channel_tag_ids.ids))

        # Status
        if self.state:
            domain.append(("state", "=", self.state))

        return domain

    # -----------------------------------
    # PUBLIC BUTTONS
    # -----------------------------------
    def action_send_all(self):
        """Button 1: Report semua complaint ikut tarikh sahaja"""
        self.ensure_one()
        domain = self._build_domain(only_dates=True)
        return self._send_report(domain, subject_prefix="[ALL Complaints]")

    def action_send_filtered(self):
        """Button 2: Report ikut semua filter"""
        self.ensure_one()
        domain = self._build_domain(only_dates=False)
        return self._send_report(domain, subject_prefix="[Filtered Complaints]")

    # -----------------------------------
    # CORE SENDER
    # -----------------------------------
    def _send_report(self, domain, subject_prefix=""):
        self.ensure_one()
        Complaint = self.env["customer.complaint"]

        complaints = Complaint.search(domain, order="date_reported, name")

        # 1) Basic counts (guna base domain utk elak double filter)
        base_domain = [
            ("date_reported", ">=", self.date_from),
            ("date_reported", "<=", self.date_to),
        ]
        total_complaints = len(complaints)
        new_count = Complaint.search_count(base_domain + [("state", "=", "new")])
        in_progress = Complaint.search_count(
            base_domain + [("state", "=", "in_progress")]
        )
        waiting = Complaint.search_count(
            base_domain + [("state", "=", "waiting_return")]
        )
        closed = Complaint.search_count(base_domain + [("state", "=", "closed")])

        # 2) By Department
        dept_summary = {}
        for c in complaints:
            dname = (
                c.x_studio_report_from_department.name
                if c.x_studio_report_from_department
                else "Unassigned"
            )
            dept_summary[dname] = dept_summary.get(dname, 0) + 1

        dept_parts = [
            "- <b>%s</b>: %s" % (name, value) for name, value in dept_summary.items()
        ]
        dept_html = "<br>".join(dept_parts) if dept_parts else "No data"

        # 3) By Complaint Type
        type_summary = {}
        for c in complaints:
            t = c.complaint_type or "Unassigned"
            type_summary[t] = type_summary.get(t, 0) + 1

        type_parts = [
            "- <b>%s</b>: %s" % (name, value) for name, value in type_summary.items()
        ]
        type_html = "<br>".join(type_parts) if type_parts else "No data"

        # 4) By Channel (guna nama tag)
        channel_summary = {}
        for c in complaints:
            if c.x_studio_channel:
                for tag in c.x_studio_channel:
                    channel_summary[tag.name] = channel_summary.get(tag.name, 0) + 1
            else:
                channel_summary["Unassigned"] = (
                    channel_summary.get("Unassigned", 0) + 1
                )

        channel_parts = [
            "- <b>%s</b>: %s" % (name, value) for name, value in channel_summary.items()
        ]
        channel_html = "<br>".join(channel_parts) if channel_parts else "No data"

        # 5) Latest 5 complaints dalam domain yang sama
        latest = Complaint.search(domain, order="create_date desc", limit=5)
        latest_parts = []
        for c in latest:
            latest_parts.append(
                "<li>%s – %s – %s</li>"
                % (
                    c.name or "",
                    c.partner_id.display_name or "",
                    c.state or "",
                )
            )
        latest_html = "".join(latest_parts) or "<li>No complaints in this period.</li>"

        # 6) Generate Excel attachment
        attachment = self._generate_excel_attachment(complaints)

        # 7) Email body
        date_from_str = format_date(self.env, self.date_from)
        date_to_str = format_date(self.env, self.date_to)

        email_body = """
        <p>Hi Boss,</p>

        <p>Here is the Monthly Complaints Report for <b>%s</b> to <b>%s</b>:</p>

        <h3>1. Summary</h3>
        <ul>
            <li><b>Total Complaints:</b> %s</li>
            <li><b>New:</b> %s</li>
            <li><b>In Progress:</b> %s</li>
            <li><b>Waiting Return Stock:</b> %s</li>
            <li><b>Closed:</b> %s</li>
        </ul>

        <h3>2. By Department</h3>
        <p>%s</p>

        <h3>3. By Complaint Type</h3>
        <p>%s</p>

        <h3>4. By Channel</h3>
        <p>%s</p>

        <h3>5. Latest Complaints</h3>
        <ul>%s</ul>

        <p>Excel file with full complaint list is attached.</p>
        <p>Please log in to Odoo for full details.</p>
        """ % (
            date_from_str,
            date_to_str,
            total_complaints,
            new_count,
            in_progress,
            waiting,
            closed,
            dept_html,
            type_html,
            channel_html,
            latest_html,
        )

        subject = "%s Monthly Complaints Report (%s → %s)" % (
            subject_prefix,
            date_from_str,
            date_to_str,
        )

        mail_vals = {
            "subject": subject,
            "body_html": email_body,
            "email_from": self.env.user.email_formatted
            or "info@morimotoformulas.com",
            "email_to": self.recipient_email,
            "attachment_ids": [(4, attachment.id)] if attachment else [],
        }
        self.env["mail.mail"].sudo().create(mail_vals).send()

        return True

    # -----------------------------------
    # EXCEL HELPER
    # -----------------------------------
    def _generate_excel_attachment(self, complaints):
        """Create Excel with all complaints in domain and return ir.attachment"""
        self.ensure_one()
        if not complaints:
            return False

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        sheet = workbook.add_worksheet("Complaints")

        header_format = workbook.add_format({"bold": True})

        headers = [
            "Complaint Number",
            "Complaint Date",
            "Customer",
            "Department",
            "Complaint Type",
            "Sub Issue",
            "Channel Tags",
            "Status",
            "Sales Order",
            "Invoice",
            "Delivery Order",
        ]

        # write header
        for col, h in enumerate(headers):
            sheet.write(0, col, h, header_format)

        # mapping selection ke teks (untuk type & status)
        Complaint = self.env["customer.complaint"]
        type_sel = dict(Complaint._fields["complaint_type"].selection)
        state_sel = dict(Complaint._fields["state"].selection)

        row = 1
        for c in complaints:
            # pilih sub-issue yang betul ikut complaint_type
            sub_issue = ""
            if c.complaint_type == "product_quality":
                sub_issue = c.x_studio_product_quality_issue or ""
            elif c.complaint_type == "delivery_issue":
                sub_issue = c.x_studio_deliveryshipping_issue or ""
            elif c.complaint_type == "billing_issue":
                sub_issue = c.x_studio_billingpayment_issue or ""
            elif c.complaint_type == "service":
                sub_issue = c.x_studio_customer_service_issue or ""

            channel_names = ", ".join(c.x_studio_channel.mapped("name")) or ""

            sheet.write(row, 0, c.name or "")
            sheet.write(
                row,
                1,
                c.date_reported
                and format_date(self.env, c.date_reported)
                or "",
            )
            sheet.write(row, 2, c.partner_id.display_name or "")
            sheet.write(
                row,
                3,
                c.x_studio_report_from_department.name
                if c.x_studio_report_from_department
                else "",
            )
            sheet.write(
                row, 4, type_sel.get(c.complaint_type, c.complaint_type or "")
            )
            sheet.write(row, 5, sub_issue)
            sheet.write(row, 6, channel_names)
            sheet.write(row, 7, state_sel.get(c.state, c.state or ""))
            sheet.write(row, 8, c.sale_order_id.name or "")
            sheet.write(row, 9, c.invoice_id.name or "")
            sheet.write(row, 10, c.picking_id.name or "")
            row += 1

        workbook.close()
        output.seek(0)
        data = output.read()

        filename = "Monthly_Complaints_%s_%s.xlsx" % (
            self.date_from,
            self.date_to,
        )

        attachment = self.env["ir.attachment"].create(
            {
                "name": filename,
                "type": "binary",
                "raw": data,
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
        )
        return attachment


        # tutup wizard
        return {"type": "ir.actions.act_window_close"}
