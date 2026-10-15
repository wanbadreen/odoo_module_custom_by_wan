{
    "name": "MotoGene Event Activity Hub",
    "summary": "Multiple-session activity builder using standard Odoo Events",
    "version": "18.0.1.2.1",
    "category": "Marketing/Events",
    "author": "WanBadreen",
    "license": "LGPL-3",
    "depends": ["website_event"],
    "data": [
        "security/ir.model.access.csv",
        "security/activity_rules.xml",
        "views/backend_views.xml",
        "views/website_templates.xml"
    ],
    "assets": {
        "web.assets_backend": [
            "motogene_event_activity_hub/static/src/js/event_create_wizard.js"
        ]
    },
    "installable": True,
    "application": False,
}
