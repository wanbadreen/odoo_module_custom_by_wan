{
    "name": "Next Order Coupon Validity (Development)",
    "version": "18.0.0.1.0",
    "category": "Sales",
    "summary": "Separate coupon issuance and fixed redemption windows",
    "license": "LGPL-3",
    "author": "WanBadreen",
    "depends": ["sale_loyalty"],
    "data": [],
    # Guardrail: not installable until issuance, redemption, archived-product
    # protections and full test coverage are implemented and reviewed.
    "installable": False,
    "application": False,
}
