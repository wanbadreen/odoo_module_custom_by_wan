# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError


class TestCouponValidity(TransactionCase):
    """Schema/opt-in validation; end-to-end SO and website tests still required."""

    def test_new_fields_exist_and_opt_in_disabled_by_default(self):
        Program = self.env["loyalty.program"]
        self.assertIn("separate_coupon_validity", Program._fields)
        self.assertIn("coupon_issuance_start_date", Program._fields)
        self.assertIn("coupon_issuance_end_date", Program._fields)
        self.assertFalse(Program._fields["separate_coupon_validity"].default(Program))
