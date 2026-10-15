{
    "name": "MotoGene Event Contacts Integration",
    "summary": "Booking owners, attendee contacts, safe matching and staff reviews",
    "version": "18.0.1.0.0",
    "author": "WanBadreen",
    "category": "Marketing/Events",
    "license": "LGPL-3",
    "depends": ["motogene_event_activity_hub", "motogene_event_self_service", "website_event", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "security/contact_rules.xml",
        "views/contact_backend_views.xml",
        "views/contact_website_templates.xml"
    ],
    "installable": True,
    "application": False,
}
