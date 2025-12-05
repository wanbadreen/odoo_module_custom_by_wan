{
    "name": "Morimoto Loyalty Partial Redeem",
    "summary": "Allow partial redemption of loyalty points on sales orders.",
    "version": "18.0.1.0.0",
    "author": "WanBadreen",
    "website": "https://github.com/wanbadreen/odoo_loyalty_partial_redeem",
    "category": "Sales",
    "license": "LGPL-3",
    "application": False,
    "depends": ["sale_management", "loyalty"],
    "data": [
        "security/ir.model.access.csv",
        "data/product_loyalty_discount.xml",
        "views/sale_order_view.xml",
        "views/loyalty_partial_redeem_wizard_view.xml",
    ],
}
