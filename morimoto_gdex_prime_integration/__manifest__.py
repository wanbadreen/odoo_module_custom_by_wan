{
    "name": "Morimoto GDEX Prime Integration",
    "version": "18.0.1.0.0",
    "summary": "Create GDEX Prime consignments from delivery orders and sync status.",
    "category": "Inventory/Delivery",
    "author": "Morimoto",
    "license": "LGPL-3",
    "depends": ["stock", "delivery"],
    "data": [
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
        "views/stock_picking_views.xml",
        "data/server_actions.xml",
        "data/ir_cron.xml",
    ],
    "installable": True,
    "application": False,
}
