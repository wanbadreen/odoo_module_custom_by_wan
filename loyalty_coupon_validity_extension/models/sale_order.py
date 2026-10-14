# -*- coding: utf-8 -*-
from odoo import models, _


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _program_check_compute_points(self, programs):
        """Block issuance only; coupon redemption remains governed by program
        date_from/date_to and the individual card expiration_date.
        """
        result = super()._program_check_compute_points(programs)
        order_date = self._get_confirmed_tx_create_date()
        for program in programs.filtered(
            lambda p: p.program_type == "next_order_coupons"
            and p.separate_coupon_validity
        ):
            if not (
                program.coupon_issuance_start_date
                <= order_date
                <= program.coupon_issuance_end_date
            ):
                result[program] = {
                    "error": _("Voucher issuance is outside the allowed period.")
                }
        return result
