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

    def _new_order(self):
        return self.env["sale.order"].create({"partner_id": self.partner.id})

    def _reward_qty(self, order):
        line = order.order_line.filtered(
            lambda l: l.is_motogene_promo_reward and l.promotion_program_id == self.program
        )
        return sum(line.mapped("product_uom_qty"))

    def test_01_three_boxes_get_two(self):
        order = self._new_order()
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.box.id,
            "product_uom_qty": 3,
            "price_unit": 100,
        })
        self.assertEqual(self._reward_qty(order), 2)

    def test_02_eight_boxes_get_four(self):
        order = self._new_order()
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.box.id,
            "product_uom_qty": 8,
            "price_unit": 100,
        })
        self.assertEqual(self._reward_qty(order), 4)

    def test_03_nine_boxes_get_six_and_downscale(self):
        order = self._new_order()
        line = self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.box.id,
            "product_uom_qty": 9,
            "price_unit": 100,
        })
        self.assertEqual(self._reward_qty(order), 6)
        line.write({"product_uom_qty": 8})
        self.assertEqual(self._reward_qty(order), 4)
        line.write({"product_uom_qty": 2})
        self.assertEqual(self._reward_qty(order), 0)

    def test_04_combo_package_uses_configured_box_units(self):
        order = self._new_order()
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.combo8.id,
            "product_uom_qty": 1,
            "price_unit": 2800,
        })
        self.assertEqual(self._reward_qty(order), 4)
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.box.id,
            "product_uom_qty": 1,
            "price_unit": 100,
        })
        self.assertEqual(self._reward_qty(order), 6)

    def test_05_reward_line_does_not_self_count(self):
        order = self._new_order()
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.box.id,
            "product_uom_qty": 3,
            "price_unit": 100,
        })
        order.action_recompute_motogene_promotions()
        order.action_recompute_motogene_promotions()
        self.assertEqual(self._reward_qty(order), 2)
        self.assertEqual(len(order.order_line.filtered("is_motogene_promo_reward")), 1)

    def test_06_expired_program_removes_reward_on_recompute(self):
        order = self._new_order()
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.box.id,
            "product_uom_qty": 3,
            "price_unit": 100,
        })
        self.assertEqual(self._reward_qty(order), 2)
        self.program.write({"date_end": fields.Date.today() - timedelta(days=1)})
        order.action_recompute_motogene_promotions()
        self.assertEqual(self._reward_qty(order), 0)
