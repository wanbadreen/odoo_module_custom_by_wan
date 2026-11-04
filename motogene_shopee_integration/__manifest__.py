{
    "name": "MotoGene Shopee Integration",
    "summary": "Shopee sandbox OAuth, product mapping, order preview and gated draft SO and Shopee Studio delivery/income integration",
    "version": "18.0.3.2.0",
    "category": "Sales/Sales",
    "author": "Morimoto Formulas",
    "license": "LGPL-3",
    "depends": ["base", "web", "product", "sale"],
    "data": [
        "security/ir.model.access.csv",
        "views/shopee_shop_views.xml",
        "views/shopee_import_views.xml",
        "views/shopee_sale_views.xml",
        "data/shopee_cron.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    "installable": True,
    "application": True,
}