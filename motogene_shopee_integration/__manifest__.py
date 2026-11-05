{
    "name": "MotoGene Shopee Integration",
    "summary": "Shopee sandbox order to quotation, escrow, and invoice reference integration",
    "version": "18.0.5.2.0",
    "category": "Sales/Sales",
    "author": "Morimoto Formulas",
    "license": "LGPL-3",
    "depends": ["base", "web", "product", "sale", "sale_stock"],
    "data": [
        "security/ir.model.access.csv",
        "views/shopee_shop_views.xml",
        "views/shopee_import_views.xml",
        "views/shopee_sale_views.xml",
        "views/shopee_picking_views.xml",
        "views/shopee_shipment_wizard_views.xml",
        "views/shopee_bulk_shipment_wizard_views.xml",
        "data/shopee_bulk_action.xml",
        "data/shopee_cron.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "motogene_shopee_integration/static/src/js/shopee_home_badge.js",
            "motogene_shopee_integration/static/src/scss/shopee_home_badge.scss",
        ],
    },
    "external_dependencies": {"python": ["requests", "pypdf"]},
    "installable": True,
    "application": True,
}