# -*- coding: utf-8 -*-
"""Read-only audit for leftover MotoGene Events shell QA artifacts.

Safe to run once in Odoo.sh STAGING. No create, write, unlink, or commit.
Odoo ORM search is used because translated names are JSONB in Odoo 18.
"""
import os

db = os.environ.get("PGDATABASE") or env.cr.dbname
if "staging" not in db.lower():
    raise RuntimeError("STOP: not a staging database (%s)" % db)

# Prefix printed in the failed V2 post-rollback audit SQL query.
latest = "qa-shell-7f068c48dd"
models = (
    ("Contacts", "res.partner", "email"),
    ("Events", "event.event", "name"),
    ("Activities", "motogene.event.activity", "name"),
    ("Tags", "res.partner.category", "name"),
    ("Bookings", "motogene.event.booking", "owner_email"),
    ("Registrations (email)", "event.registration", "email"),
    ("Registrations (name)", "event.registration", "name"),
)

print("===== MotoGene Staging QA: READ-ONLY AUDIT =====")
for label, model, field in models:
    records = env[model].sudo().search([(field, "=ilike", latest + "%")], limit=50)
    total = env[model].sudo().search_count([(field, "=ilike", latest + "%")])
    print("V2 %s: count=%s ids=%s" % (label, total, records.ids))

print("\nPossible earlier-run artifacts (all qa-shell-* prefixes):")
for label, model, field in models:
    records = env[model].sudo().search([(field, "=ilike", "qa-shell-%")], limit=50)
    total = env[model].sudo().search_count([(field, "=ilike", "qa-shell-%")])
    if total:
        print("OLD/ALL %s: count=%s ids=%s" % (label, total, records.ids))

print("\nPreviously reported IDs (inspect only, no deletion):")
for label, model, record_ids in (
    ("Contact", "res.partner", [23483]),
    ("Event", "event.event", [23]),
    ("Tags", "res.partner.category", [42, 43]),
):
    rows = env[model].sudo().browse(record_ids).exists()
    print("%s:" % label)
    for row in rows:
        if model == "res.partner":
            detail = "%s | %s" % (row.name, row.email or "")
        elif model == "event.event":
            detail = "%s | registrations=%s" % (
                row.name, len(row.registration_ids)
            )
        else:
            detail = row.name
        print("  id=%s %s" % (row.id, detail))

print("\nAUDIT COMPLETE. READ-ONLY. No rows were deleted.")
