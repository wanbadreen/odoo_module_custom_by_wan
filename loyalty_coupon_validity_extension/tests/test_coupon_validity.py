# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError


class TestCouponValidity(TransactionCase):
    def test_fields_and_legacy_mode(self):
        Program = self.env["loyalty.program"]
        self.assertIn("separate_coupon_validity", Program._fields)
        self.assertIn("coupon_issuance_start_date", Program._fields)
        self.assertIn("coupon_issuance_end_date", Program._fields)

    def test_archived_product_restriction_is_not_lost(self):
        product = self.env["product.product"].create({
            "name": "Archive-Protected Voucher Item",
            "sale_ok": True,
        })
        program = self.env["loyalty.program"].create({
            "name": "Archive Protected Coupon Test",
            "program_type": "next_order_coupons",
        })
        rule = program.rule_ids[:1]
        if not rule:
            rule = self.env["loyalty.rule"].create({
                "program_id": program.id,
                "product_ids": [(6, 0, [product.id])],
            })
        else:
            rule.product_ids = product
        product.active = False
        domain = rule._get_valid_product_domain()
        self.assertTrue(domain)
        other = self.env["product.product"].create({
            "name": "Unrelated Product", "sale_ok": True,
        })
        self.assertFalse(other.filtered_domain(domain))
