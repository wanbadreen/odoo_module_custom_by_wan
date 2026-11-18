{
    "name": "MotoGene FedEx Shipping Quotation",
    "version": "18.0.1.0.0",
    "category": "Inventory/Delivery",
    "summary": "FedEx international rate quotation wizard (sandbox only)",
    "author": "MotoGene",
    "license": "LGPL-3",
    "depends": ["sale_management", "stock", "delivery"],
    "data": [
        "security/fedex_groups.xml",
        "security/ir.model.access.csv",
        "views/fedex_quote_views.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    "installable": True,
    "application": False,
    "auto_install": False,
}
