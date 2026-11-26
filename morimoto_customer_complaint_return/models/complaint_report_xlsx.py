# morimoto_customer_complaint_return/models/complaint_report_xlsx.py

from odoo import models
import xlsxwriter
from io import BytesIO
import base64
from datetime import datetime, date  # <-- tambah import ni

class CustomerComplaint(models.Model):
    _inherit = "customer.complaint"

    def _export_monthly_complaints_xlsx(self, domain, date_from, date_to):
        """
        Dipanggil dari Server Action (wizard x_monthly_complaint_re)
        untuk hasilkan attachment Excel berdasarkan domain yang sama
        dengan report email.
        """
        complaints = self.search(domain)

        # Siapkan workbook
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        sheet = workbook.add_worksheet("Complaints")

        header_fmt = workbook.add_format({"bold": True, "bg_color": "#DDDDDD"})
        wrap_fmt = workbook.add_format({"text_wrap": True})

        # Helper utk dapatkan label selection (bukan code)
        def sel_label(field_name, value):
            field = self._fields.get(field_name)
            if not field:
                return value or ""
            mapping = dict(field.selection)
            return mapping.get(value, value or "")

        # ------------- Header columns (ikut export kau) -------------
        headers = [
            "Complaint Date",                    # date_reported
            "Complaint Number",                  # name
            "Customer",                          # partner_id
            "Channel",                           # x_studio_channel names / channel
            "Sales Order/Display Name",          # sale_order_id
            "Delivery Order",                    # picking_id
            "Invoice",                           # invoice_id
            "Complaint Type",                    # complaint_type (label)
            "Product Quality Issue",             # x_studio_product_quality_issue
            "Delivery/Shipping Issue",           # x_studio_deliveryshipping_issue
            "Billing/Payment Issue",             # x_studio_billingpayment_issue
            "Customer Service Issue",            # x_studio_customer_service_issue
            "Status",                            # state (label)
            "Complaint Description",             # description
            "Resolution / Follow-up",            # resolution
            "Internal Notes",                    # internal_note
            "Returned Products/Product",         # return_line_ids.product_id
            "Returned Products/Returned Qty",    # return_line_ids.quantity_returned
        ]

        for col, title in enumerate(headers):
            sheet.write(0, col, title, header_fmt)

        # ------------- Data rows -------------
        row = 1
        for c in complaints:
            # Channel: join semua tag name, kalau kosong guna field selection lama
            if c.x_studio_channel:
                channel_text = ", ".join(
                    [t.name for t in c.x_studio_channel if t.name]
                )
            else:
                channel_text = sel_label("channel", c.channel)

            # Complaint Type & Status label
            complaint_type = sel_label("complaint_type", c.complaint_type)
            status_label = sel_label("state", c.state)

            # Sub-issues (selection Studio – biasanya value == label)
            pq_issue = sel_label(
                "x_studio_product_quality_issue", c.x_studio_product_quality_issue
            )
            del_issue = sel_label(
                "x_studio_deliveryshipping_issue", c.x_studio_deliveryshipping_issue
            )
            bill_issue = sel_label(
                "x_studio_billingpayment_issue", c.x_studio_billingpayment_issue
            )
            cs_issue = sel_label(
                "x_studio_customer_service_issue", c.x_studio_customer_service_issue
            )

            # Returned products – join jadi multi-line string
            product_lines = []
            qty_lines = []
            for line in c.return_line_ids:
                pname = line.product_id.display_name or ""
                product_lines.append(pname)
                qty_lines.append(str(line.quantity_returned or 0))

            product_text = "\n".join(product_lines)
            qty_text = "\n".join(qty_lines)

            # ====== FORMAT TARIKH DI SINI ======
            date_val = c.date_reported
            if date_val:
                # date_val kadang date object, kadang string "YYYY-MM-DD"
                if isinstance(date_val, str):
                    try:
                        d = datetime.strptime(date_val, "%Y-%m-%d").date()
                        date_str = d.strftime("%d/%m/%Y")
                    except ValueError:
                        # fallback – kalau format pelik, pakai apa yang ada
                        date_str = date_val
                elif isinstance(date_val, date):
                    date_str = date_val.strftime("%d/%m/%Y")
                else:
                    date_str = str(date_val)
            else:
                date_str = ""

            sheet.write(row, 0, date_str)  # <-- guna string, bukan raw value
            sheet.write(row, 1, c.name or "")
            sheet.write(row, 2, c.partner_id.display_name or "")
            sheet.write(row, 3, channel_text or "")
            sheet.write(row, 4, c.sale_order_id.name or "")
            sheet.write(row, 5, c.picking_id.name or "")
            sheet.write(row, 6, c.invoice_id.name or "")
            sheet.write(row, 7, complaint_type or "")
            sheet.write(row, 8, pq_issue or "")
            sheet.write(row, 9, del_issue or "")
            sheet.write(row, 10, bill_issue or "")
            sheet.write(row, 11, cs_issue or "")
            sheet.write(row, 12, status_label or "")
            sheet.write(row, 13, c.description or "", wrap_fmt)
            sheet.write(row, 14, c.resolution or "", wrap_fmt)
            sheet.write(row, 15, c.internal_note or "", wrap_fmt)
            sheet.write(row, 16, product_text or "", wrap_fmt)
            sheet.write(row, 17, qty_text or "", wrap_fmt)

            row += 1

        # Auto set column width sikit basic
        sheet.set_column(0, 0, 12)   # date
        sheet.set_column(1, 2, 18)   # number + customer
        sheet.set_column(3, 7, 20)   # channel..complaint type
        sheet.set_column(8, 11, 22)  # sub issues
        sheet.set_column(13, 15, 40) # descriptions
        sheet.set_column(16, 17, 25) # return lines

        workbook.close()
        xlsx_data = output.getvalue()

        filename = "Monthly_Complaints_%s_%s.xlsx" % (date_from, date_to)

        attachment = self.env["ir.attachment"].create({
            "name": filename,
            "type": "binary",
            "raw": xlsx_data,
            "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        })

        return attachment
