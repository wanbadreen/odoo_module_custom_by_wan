# -*- coding: utf-8 -*-
"""Preliminary configuration model only.

These fields do NOT yet enforce coupon issuance or redemption.
Enable this module only after runtime hooks and regression tests are added.
"""
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class LoyaltyProgram(models.Model):
    _inherit = "loyalty.program"

    separate_coupon_validity = fields.Boolean(
        string="Separate Voucher Issuance and Redemption",
        default=False,
        help="Opt-in for Next Order Coupons. Legacy programs retain their original behavior.",
    )
    coupon_issuance_start_date = fields.Date(string="Issuance Start Date")
    coupon_issuance_end_date = fields.Date(string="Issuance End Date")
    coupon_redemption_start_date = fields.Date(string="Redemption Start Date")
    coupon_redemption_end_date = fields.Date(
        string="Fixed Redemption Expiry Date",
        help="Shared fixed end date for coupons issued by this program.",
    )

    @api.constrains(
        "separate_coupon_validity",
        "program_type",
        "coupon_issuance_start_date",
        "coupon_issuance_end_date",
        "coupon_redemption_start_date",
        "coupon_redemption_end_date",
    )
    def _check_coupon_validity_dates(self):
        for program in self:
            if not program.separate_coupon_validity:
                continue
            if program.program_type != "next_order_coupons":
                raise ValidationError(_("Separate coupon validity applies only to Next Order Coupons."))
            dates = (
                program.coupon_issuance_start_date,
                program.coupon_issuance_end_date,
                program.coupon_redemption_start_date,
                program.coupon_redemption_end_date,
            )
            if not all(dates):
                raise ValidationError(_("Complete all issuance and redemption dates before enabling separate validity."))
            issuance_start, issuance_end, redemption_start, redemption_end = dates
            if issuance_start > issuance_end:
                raise ValidationError(_("Issuance start date cannot be later than issuance end date."))
            if redemption_start > redemption_end:
                raise ValidationError(_("Redemption start date cannot be later than redemption end date."))
            if redemption_end < issuance_start:
                raise ValidationError(_("Redemption cannot end before issuance starts."))
