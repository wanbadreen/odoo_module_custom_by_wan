# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMotogenePromotionCustomerExclusions(TransactionCase):

    def setUp(self):
        super().setUp()

        Product = self.env["product.product"]
        PartnerCategory = self.env["res.partner.category"]

        self.product = Product.create({
            "name": "Eligible Promo Product",
            "type": "consu",
            "sale_ok": True,
            "list_price": 100.0,
        })
        self.reward = Product.create({
            "name": "Promo Reward",
            "type": "consu",
            "sale_ok": True,
            "list_price": 10.0,
        })

        self.dealer_tag = PartnerCategory.create({"name": "Dealer"})
        self.staff_tag = PartnerCategory.create({"name": "Staff"})
        self.shareholder_tag = PartnerCategory.create({"name": "Shareholder"})

        today = fields.Date.today()
        self.program = self.env["motogene.promotion.program"].create({
            "name": "Exclude Internal Customer Tags",
            "state": "active",
            "date_start": today - timedelta(days=1),
            "date_end": today + timedelta(days=30),
            "rule_type": "every_x_qty",
            "threshold_qty": 1,
            "repeat_reward": True,
            "reward_product_id": self.reward.id,
            "reward_qty": 1,
            "excluded_partner_tag_ids": [(6, 0, [
                self.dealer_tag.id,
                self.staff_tag.id,
                self.shareholder_tag.id,
            ])],
            "eligibility_line_ids": [
                (0, 0, {
                    "product_tmpl_id": self.product.product_tmpl_id.id,
                    "box_units_per_qty": 1,
                }),
            ],
        })

    def _make_order(self, partner):
        order = self.env["sale.order"].create({"partner_id": partner.id})
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.product.id,
            "product_uom_qty": 1,
            "price_unit": 100,
        })
        return order

    def _reward_qty(self, order):
        lines = order.order_line.filtered(
            lambda line: line.is_motogene_promo_reward
            and line.promotion_program_id == self.program
        )
        return sum(lines.mapped("product_uom_qty"))

    def test_01_normal_customer_is_eligible(self):
        partner = self.env["res.partner"].create({"name": "Normal Customer"})
        order = self._make_order(partner)
        self.assertEqual(self._reward_qty(order), 1)

    def test_02_excluded_customer_tag_blocks_promotion(self):
        partner = self.env["res.partner"].create({
            "name": "Dealer Customer",
            "category_id": [(6, 0, [self.dealer_tag.id])],
        })
        order = self._make_order(partner)
        self.assertEqual(self._reward_qty(order), 0)

    def test_03_commercial_parent_tag_also_blocks_promotion(self):
        company_partner = self.env["res.partner"].create({
            "name": "Staff Company",
            "is_company": True,
            "category_id": [(6, 0, [self.staff_tag.id])],
        })
        contact = self.env["res.partner"].create({
            "name": "Staff Contact",
            "parent_id": company_partner.id,
        })
        order = self._make_order(contact)
        self.assertEqual(self._reward_qty(order), 0)

    def test_04_changing_customer_recomputes_reward(self):
        normal = self.env["res.partner"].create({"name": "Normal Customer 2"})
        excluded = self.env["res.partner"].create({
            "name": "Shareholder Customer",
            "category_id": [(6, 0, [self.shareholder_tag.id])],
        })

        order = self._make_order(normal)
        self.assertEqual(self._reward_qty(order), 1)

        order.write({"partner_id": excluded.id})
        self.assertEqual(self._reward_qty(order), 0)
