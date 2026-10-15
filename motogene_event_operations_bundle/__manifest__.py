{
    "name": "MotoGene Events Operations Bundle",
    "summary": "Combined event Excel reporting, optional staff activities, and email readiness review",
    "version": "18.0.1.0.0",
    "category": "Marketing/Events",
    "author": "WanBadreen",
    "license": "LGPL-3",
    "depends": [
        "motogene_event_daily_summary",
        "motogene_event_contacts",
        "motogene_event_activity_hub",
        "motogene_event_self_service",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/event_operations_views.xml",
        "views/event_export_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
