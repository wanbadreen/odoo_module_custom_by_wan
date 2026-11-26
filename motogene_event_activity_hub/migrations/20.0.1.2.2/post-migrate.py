"""Backfill private per-session identity after Odoo migrates question links to M2M.

Never reassign or recreate attendee question/answer rows; require a manual
review for multi-event children rather than guessing which event owns them.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    children = env["event.question"].with_context(active_test=False).search([
        ("motogene_activity_source_id", "!=", False),
        ("motogene_activity_session_event_id", "=", False),
    ])
    for child in children:
        events = child.event_ids
        if len(events) > 1:
            raise ValueError(
                "Activity session question %s is linked to multiple Events; "
                "manual migration review required." % child.id
            )
        if events:
            child.write({"motogene_activity_session_event_id": events.id})
