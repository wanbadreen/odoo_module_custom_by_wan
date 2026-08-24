# -*- coding: utf-8 -*-
{
    "name": "MotoGene Promotion Engine",
    "version": "18.0.1.3.1",
    "category": "Sales/Sales",
    "summary": "Configurable conditional promotion engine for MotoGene sales orders.",
    "author": "WanBadreen",
    "license": "LGPL-3",
    "depends": ["sale_management", "product"],
    "data": [
        "security/ir.model.access.csv",
        "views/promotion_program_views.xml",
        "views/promotion_exclusion_views.xml",
        "views/sale_order_views.xml",
    ],
    "demo": [],
    "installable": True,
    "application": True,
}
