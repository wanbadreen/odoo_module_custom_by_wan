# -*- coding: utf-8 -*-

from odoo import fields, models


SKIP_CTX = "motogene_skip_promotion_engine"


class MotogenePromotionProgram(models.Model):
    _inherit = "motogene.promotion.program"

    excluded_partner_tag_ids = fields.Many2many(
        "res.partner.category",
        "motogene_promo_program_excluded_partner_tag_rel",
        "program_id",
        "category_id",
        string="Excluded Customer Tags",
        help=(
            "Customers carrying any of these Contact Tags are excluded from this promotion. "
            "Tags on the customer's commercial parent are also checked."
        ),
    )

    def _is_valid_for_order(self, order):
        self.ensure_one()

        if not super()._is_valid_for_order(order):
            return False

        if not self.excluded_partner_tag_ids or not order.partner_id:
            return True

        partner = order.partner_id
        partner_tag_ids = set(partner.category_id.ids)

        commercial_partner = partner.commercial_partner_id
        if commercial_partner and commercial_partner != partner:
            partner_tag_ids.update(commercial_partner.category_id.ids)

        excluded_tag_ids = set(self.excluded_partner_tag_ids.ids)
        return not bool(partner_tag_ids.intersection(excluded_tag_ids))


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def write(self, vals):
        res = super().write(vals)

        # Customer eligibility can change when the quotation customer changes.
        if (
            "partner_id" in vals
            and not self.env.context.get(SKIP_CTX)
        ):
            self.filtered(
                lambda order: order.state in ("draft", "sent")
            )._apply_motogene_promotions()

        return res
