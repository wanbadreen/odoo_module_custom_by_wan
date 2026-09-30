# -*- coding: utf-8 -*-
{
    "name": "MotoGene Promotion Engine",
    "version": "18.0.1.9.0",
    "category": "Sales/Sales",
    "summary": "Configurable conditional promotion engine for MotoGene sales orders.",
    "author": "WanBadreen",
    "license": "LGPL-3",
    "depends": ["sale_management", "sale_stock", "product"],
    "data": [
        "security/ir.model.access.csv",
        "views/promotion_program_views.xml",
        "views/promotion_exclusion_views.xml",
        "views/scratch_shortage_wizard_views.xml",
        "views/sale_order_views.xml",
        "views/stock_picking_views.xml",
        "report/scratch_picking_report.xml",
    ],
    "demo": [],
    "installable": True,
    "application": True,
}
