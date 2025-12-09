{
    "name": "Morimoto Loyalty Partial Redeem",
    "summary": "Allow partial redemption of loyalty points on sales orders.",
    "version": "18.0.1.0.0",
    "author": "WanBadreen",
    "website": "",
    "category": "Sales",
    "license": "LGPL-3",
    "application": False,
    "depends": ["sale_management", "loyalty", "website_sale"],
    "data": [
        "security/ir.model.access.csv",
        "views/sale_order_view.xml",
        "views/loyalty_partial_redeem_wizard_view.xml",
        "views/website_sale_loyalty_templates.xml",
    ],
    "images": ["images/main_screenshot.png"],
}
