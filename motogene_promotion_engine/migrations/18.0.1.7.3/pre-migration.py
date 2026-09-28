"""Drop inherited OCR views before Odoo validates the new picking form."""


def migrate(cr, version):
    for xml_name, table in (
        ("view_picking_form_motogene_scratch_packing", "ir_ui_view"),
        ("view_motogene_scratch_ocr_wizard_form", "ir_ui_view"),
        ("access_motogene_scratch_ocr_wizard_user", "ir_model_access"),
    ):
        cr.execute(
            "SELECT res_id FROM ir_model_data WHERE module = %s AND name = %s",
            ("motogene_promotion_engine", xml_name),
        )
        row = cr.fetchone()
        if row:
            cr.execute(f"DELETE FROM {table} WHERE id = %s", (row[0],))
            cr.execute(
                "DELETE FROM ir_model_data WHERE module = %s AND name = %s",
                ("motogene_promotion_engine", xml_name),
            )
