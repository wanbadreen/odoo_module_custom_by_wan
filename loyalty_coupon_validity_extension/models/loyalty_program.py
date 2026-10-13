# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class LoyaltyProgram(models.Model):
    _inherit = "loyalty.program"

    separate_coupon_validity = fields.Boolean(
        string="Separate Voucher Issuance and Redemption",
        default=False,
        help="Opt in per Next Order Coupon program. Existing programs stay unchanged.",
    )
    coupon_issuance_start_date = fields.Date(string="Issuance Start Date")
    coupon_issuance_end_date = fields.Date(string="Issuance End Date")

    @api.constrains(
        "separate_coupon_validity", "program_type",
        "coupon_issuance_start_date", "coupon_issuance_end_date",
        "date_from", "date_to",
    )
    def _check_coupon_validity_dates(self):
        for program in self:
            if not program.separate_coupon_validity:
                continue
            if program.program_type != "next_order_coupons":
                raise ValidationError(
                    _("Separate validity only supports Next Order Coupons.")
                )
            issuance_start = program.coupon_issuance_start_date
            issuance_end = program.coupon_issuance_end_date
            redemption_start = program.date_from
            redemption_end = program.date_to
            if not all((issuance_start, issuance_end, redemption_start, redemption_end)):
                raise ValidationError(
                    _("Set both issuance dates and both redemption dates first.")
                )
            if issuance_start > issuance_end:
                raise ValidationError(_("Issuance end must not precede issuance start."))
            if redemption_start > redemption_end:
                raise ValidationError(_("Redemption end must not precede redemption start."))
            if redemption_end < issuance_start:
                raise ValidationError(_("Redemption cannot expire before issuance begins."))
