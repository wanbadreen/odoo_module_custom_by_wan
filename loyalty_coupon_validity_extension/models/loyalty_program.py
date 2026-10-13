# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class LoyaltyProgram(models.Model):
    _inherit = "loyalty.program"

    separate_coupon_validity = fields.Boolean(
        string="Enable Separate Coupon Validity",
        default=False,
        help="Enable a separate issuance window for Next Order Coupons. "
             "Existing programs are unchanged until enabled.",
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
                raise ValidationError(_("Separate validity only supports Next Order Coupons."))
            issue_start = program.coupon_issuance_start_date
            issue_end = program.coupon_issuance_end_date
            redeem_start = program.date_from
            redeem_end = program.date_to
            if not all((issue_start, issue_end, redeem_start, redeem_end)):
                raise ValidationError(_("Specify issuance start/end and redemption start/end."))
            if issue_start > issue_end:
                raise ValidationError(_("Issuance start must be before or equal to issuance end."))
            # The standard Odoo 18 program domain checks date_from even when
            # it is creating new coupons. A later redemption start would make
            # coupon issuance impossible; reject this unsupported configuration.
            if redeem_start > issue_start:
                raise ValidationError(_(
                    "Redemption Start Date must be on or before Issuance Start Date "
                    "in this version."
                ))
            if redeem_end < redeem_start or redeem_end < issue_end:
                raise ValidationError(_(
                    "Redemption End Date must be on or after both the redemption "
                    "start and issuance end dates."
                ))
