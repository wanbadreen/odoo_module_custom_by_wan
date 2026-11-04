{
    "name": "MotoGene Shopee Integration (OAuth Foundation)",
    "summary": "Shopee Open Platform V2 sandbox authorization and token management",
    "version": "18.0.1.0.0",
    "category": "Sales/Sales",
    "author": "Morimoto Formulas",
    "license": "LGPL-3",
    "depends": ["base", "web"],
    "data": [
        "security/ir.model.access.csv",
        "views/shopee_shop_views.xml",
        "data/shopee_cron.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    "installable": True,
    "application": True,
}