{
    "name": "MotoGene Event Self-Service Cancellation",
    "summary": "Secure guest email verification and free event cancellation before start",
    "version": "18.0.1.2.0",
    "category": "Marketing/Events",
    "author": "WanBadreen",
    "license": "LGPL-3",
    "depends": ["website_event", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "views/self_service_config_views.xml",
        "views/self_service_website_templates.xml",
    ],
    "installable": True,
    "application": False,
}
