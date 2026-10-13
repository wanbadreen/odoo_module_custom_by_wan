# -*- coding: utf-8 -*-
from odoo import models


class LoyaltyRule(models.Model):
    _inherit = "loyalty.rule"

    def _get_valid_product_domain(self):
        """Keep explicit archived product IDs in eligibility filtering.

        The standard implementation checks self.product_ids with active_test
        enabled. When every linked variant is archived, that recordset appears
        empty and Odoo incorrectly interprets the rule as unrestricted.
        This preserves existing OR semantics with categories, tags and
        product_domain while excluding unrelated products.
        """
        self.ensure_one()
        if self.program_type != "next_order_coupons":
            return super()._get_valid_product_domain()
        return super(LoyaltyRule, self.with_context(active_test=False))._get_valid_product_domain()
