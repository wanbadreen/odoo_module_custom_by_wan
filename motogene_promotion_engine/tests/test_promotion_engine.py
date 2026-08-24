# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMotogenePromotionEngine(TransactionCase):

    def setUp(self):
        super().setUp()
        Product = self.env["product.product"]
        self.partner = self.env["res.partner"].create({"name": "Promo Test Customer"})
        self.company_2 = self.env["res.company"].create({
            "name": "Promo Test Company 2",
            "currency_id": self.env.company.currency_id.id,
        })
        self.box = Product.create({
            "name": "MotoGene Standard Box",
            "type": "consu",
            "sale_ok": True,
            "list_price": 100.0,
        })
        self.combo8 = Product.create({
            "name": "BetSeries 8 Box Package",
            "type": "consu",
            "sale_ok": True,
            "list_price": 2800.0,
        })
        self.reward = Product.create({
            "name": "KoraGene Sachet",
            "type": "consu",
            "sale_ok": True,
            "list_price": 10.0,
        })
        self.towel = Product.create({
            "name": "MotoGene Towel",
            "type": "consu",
            "sale_ok": True,
            "list_price": 25.0,
        })
        self.koragene_box = Product.create({
            "name": "KoraGene Box",
            "type": "consu",
            "sale_ok": True,
            "list_price": 700.0,
        })
        self.oshino = Product.create({
            "name": "Oshino Collagen",
            "type": "consu",
            "sale_ok": True,
            "list_price": 89.0,
        })
        self.redemption_product = Product.create({
            "name": "Loyalty Point Redemption",
            "default_code": "Loyalty Point Redemption",
            "type": "service",
            "sale_ok": True,
            "list_price": 0.0,
        })
        self.shipping_product = Product.create({
            "name": "Delivery Charge",
            "type": "service",
            "sale_ok": True,
            "list_price": 60.0,
        })

        today = fields.Date.today()
        self.program = self.env["motogene.promotion.program"].create({
            "name": "Every 3 Boxes Get 2 KoraGene",
            "state": "active",
            "date_start": today - timedelta(days=1),
            "date_end": today + timedelta(days=30),
            "threshold_qty": 3,
            "repeat_reward": True,
            "reward_product_id": self.reward.id,
            "reward_qty": 2,
            "eligibility_line_ids": [
                (0, 0, {"product_tmpl_id": self.box.product_tmpl_id.id, "box_units_per_qty": 1}),
                (0, 0, {"product_tmpl_id": self.combo8.product_tmpl_id.id, "box_units_per_qty": 8}),
            ],
        })

        self.minimum_program = self.env["motogene.promotion.program"].create({
            "name": "Spend 2000 Get Towel",
            "state": "active",
            "date_start": today - timedelta(days=1),
            "date_end": today + timedelta(days=30),
            "rule_type": "minimum_purchase",
            "minimum_amount": 2000,
            "amount_basis": "after_discount",
            "loyalty_redemption_handling": "ignore",
            "shipping_handling": "exclude",
            "repeat_reward": False,
            "reward_product_id": self.towel.id,
            "reward_qty": 1,
        })

        self.pwp_program = self.env["motogene.promotion.program"].create({
            "name": "KoraGene Unlocks Oshino PWP",
            "state": "active",
            "date_start": today - timedelta(days=1),
            "date_end": today + timedelta(days=30),
            "rule_type": "pwp",
            "pwp_trigger_product_tmpl_ids": [(6, 0, [self.koragene_box.product_tmpl_id.id])],
            "pwp_trigger_qty": 1,
            "pwp_buy_qty": 1,
            "pwp_repeat_per_trigger": True,
            "reward_product_id": self.oshino.id,
            "reward_qty": 1,
            "reward_line_label": "PWP Free Product",
        })

    def _new_order(self, company=None):
        vals = {"partner_id": self.partner.id}
        if company:
            vals["company_id"] = company.id
        return self.env["sale.order"].create(vals)

    def _reward_qty(self, order, program=None):
        program = program or self.program
        lines = order.order_line.filtered(
            lambda l: l.is_motogene_promo_reward and l.promotion_program_id == program
        )
        return sum(lines.mapped("product_uom_qty"))

    def _add_line(self, order, product, qty, price):
        return self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": product.id,
            "product_uom_qty": qty,
            "price_unit": price,
        })

    def test_01_three_boxes_get_two(self):
        order = self._new_order()
        self._add_line(order, self.box, 3, 100)
        self.assertEqual(self._reward_qty(order), 2)

    def test_02_eight_boxes_get_four(self):
        order = self._new_order()
        self._add_line(order, self.box, 8, 100)
        self.assertEqual(self._reward_qty(order), 4)

    def test_03_nine_boxes_get_six_and_downscale(self):
        order = self._new_order()
        line = self._add_line(order, self.box, 9, 100)
        self.assertEqual(self._reward_qty(order), 6)
        line.write({"product_uom_qty": 8})
        self.assertEqual(self._reward_qty(order), 4)
        line.write({"product_uom_qty": 2})
        self.assertEqual(self._reward_qty(order), 0)

    def test_04_combo_package_uses_configured_box_units(self):
        order = self._new_order()
        self._add_line(order, self.combo8, 1, 2800)
        self.assertEqual(self._reward_qty(order), 4)
        self._add_line(order, self.box, 1, 100)
        self.assertEqual(self._reward_qty(order), 6)

    def test_05_reward_line_does_not_self_count(self):
        order = self._new_order()
        self._add_line(order, self.box, 3, 100)
        order.action_recompute_motogene_promotions()
        order.action_recompute_motogene_promotions()
        self.assertEqual(self._reward_qty(order), 2)
        self.assertEqual(len(order.order_line.filtered("is_motogene_promo_reward")), 1)

    def test_06_expired_program_removes_reward_on_recompute(self):
        order = self._new_order()
        self._add_line(order, self.box, 3, 100)
        self.assertEqual(self._reward_qty(order), 2)
        self.program.write({"date_end": fields.Date.today() - timedelta(days=1)})
        order.action_recompute_motogene_promotions()
        self.assertEqual(self._reward_qty(order), 0)

    def test_07_minimum_purchase_after_discount(self):
        order = self._new_order()
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.combo8.id,
            "product_uom_qty": 1,
            "price_unit": 2100,
            "discount": 10,
        })
        self.assertEqual(self._reward_qty(order, self.minimum_program), 0)
        self.minimum_program.write({"amount_basis": "before_discount"})
        order.action_recompute_motogene_promotions()
        self.assertEqual(self._reward_qty(order, self.minimum_program), 1)

    def test_08_loyalty_redemption_can_be_ignored_or_deducted(self):
        order = self._new_order()
        self._add_line(order, self.combo8, 1, 2100)
        redemption_vals = {
            "order_id": order.id,
            "product_id": self.redemption_product.id,
            "product_uom_qty": 1,
            "price_unit": -500,
        }
        if "is_loyalty_redeem_line" in self.env["sale.order.line"]._fields:
            redemption_vals["is_loyalty_redeem_line"] = True
        self.env["sale.order.line"].create(redemption_vals)

        self.assertEqual(self._reward_qty(order, self.minimum_program), 1)

        self.minimum_program.write({"loyalty_redemption_handling": "deduct"})
        order.action_recompute_motogene_promotions()
        self.assertEqual(self._reward_qty(order, self.minimum_program), 0)

    def test_09_shipping_exclude_and_include(self):
        order = self._new_order()
        self._add_line(order, self.combo8, 1, 1950)
        shipping_vals = {
            "order_id": order.id,
            "product_id": self.shipping_product.id,
            "product_uom_qty": 1,
            "price_unit": 60,
        }
        if "is_delivery" in self.env["sale.order.line"]._fields:
            shipping_vals["is_delivery"] = True
        shipping_line = self.env["sale.order.line"].create(shipping_vals)

        if "is_delivery" in shipping_line._fields:
            self.assertEqual(self._reward_qty(order, self.minimum_program), 0)
            self.minimum_program.write({"shipping_handling": "include"})
            order.action_recompute_motogene_promotions()
            self.assertEqual(self._reward_qty(order, self.minimum_program), 1)

    def test_10_repeat_minimum_purchase_reward(self):
        self.minimum_program.write({"repeat_reward": True})
        order = self._new_order()
        self._add_line(order, self.combo8, 1, 4100)
        self.assertEqual(self._reward_qty(order, self.minimum_program), 2)

    def test_11_multiple_programs_can_stack(self):
        order = self._new_order()
        self._add_line(order, self.combo8, 1, 2800)
        self.assertEqual(self._reward_qty(order, self.program), 4)
        self.assertEqual(self._reward_qty(order, self.minimum_program), 1)

    def test_12_company_specific_program_does_not_apply_to_other_company(self):
        order = self._new_order(self.company_2)
        self._add_line(order, self.box, 3, 100)
        self.assertEqual(self._reward_qty(order, self.program), 0)

    def test_13_blank_company_program_applies_to_all_companies(self):
        self.program.write({"company_id": False})
        order = self._new_order(self.company_2)
        self._add_line(order, self.box, 3, 100)
        self.assertEqual(self._reward_qty(order, self.program), 2)

    def test_14_pwp_requires_paid_pwp_product_selection(self):
        order = self._new_order()
        self._add_line(order, self.koragene_box, 1, 700)
        self.assertEqual(self._reward_qty(order, self.pwp_program), 0)

        self._add_line(order, self.oshino, 1, 89)
        self.assertEqual(self._reward_qty(order, self.pwp_program), 1)

    def test_15_one_trigger_caps_pwp_reward_even_if_more_pwp_products_bought(self):
        order = self._new_order()
        self._add_line(order, self.koragene_box, 1, 700)
        self._add_line(order, self.oshino, 3, 89)
        self.assertEqual(self._reward_qty(order, self.pwp_program), 1)

    def test_16_two_triggers_unlock_two_pwp_rewards(self):
        order = self._new_order()
        trigger_line = self._add_line(order, self.koragene_box, 2, 700)
        pwp_line = self._add_line(order, self.oshino, 2, 89)
        self.assertEqual(self._reward_qty(order, self.pwp_program), 2)

        trigger_line.write({"product_uom_qty": 1})
        self.assertEqual(self._reward_qty(order, self.pwp_program), 1)

        pwp_line.write({"product_uom_qty": 0})
        self.assertEqual(self._reward_qty(order, self.pwp_program), 0)

    def test_17_pwp_repeat_can_be_limited_to_one_entitlement(self):
        self.pwp_program.write({"pwp_repeat_per_trigger": False})
        order = self._new_order()
        self._add_line(order, self.koragene_box, 3, 700)
        self._add_line(order, self.oshino, 3, 89)
        self.assertEqual(self._reward_qty(order, self.pwp_program), 1)

    def test_18_pwp_can_stack_with_other_promotions(self):
        order = self._new_order()
        self._add_line(order, self.combo8, 1, 2800)
        self._add_line(order, self.koragene_box, 1, 700)
        self._add_line(order, self.oshino, 1, 89)
        self.assertEqual(self._reward_qty(order, self.program), 4)
        self.assertEqual(self._reward_qty(order, self.minimum_program), 1)
        self.assertEqual(self._reward_qty(order, self.pwp_program), 1)
