{
    "name": "Next Order Coupon Validity (Development)",
    "version": "18.0.0.2.0",
    "category": "Sales",
    "summary": "Separate next-order coupon issuance and fixed redemption validity",
    "license": "LGPL-3",
    "author": "WanBadreen",
    "depends": ["sale_loyalty"],
    "data": ["views/loyalty_program_views.xml"],
    # Remains disabled until staging installation/regression and delayed
    # redemption-start handling are signed off.
    "installable": False,
    "application": False,
}
