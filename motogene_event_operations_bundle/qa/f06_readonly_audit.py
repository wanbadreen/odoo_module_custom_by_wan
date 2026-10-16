# -*- coding: utf-8 -*-
"""F06 MotoGene Events: read-only staging audit. No database writes or sending."""
import os
from collections import Counter
from datetime import datetime
from odoo import fields

db = os.environ.get("PGDATABASE") or env.cr.dbname
if "staging" not in db.lower():
    raise RuntimeError("STOP: this audit is STAGING ONLY (%s)" % db)
def search(model, domain):
    return env[model].sudo().with_context(active_test=False).search(domain)
def show(level, code, description):
    results.append((level, code, description))
    print("%-9s %-15s %s" % (level, code, description), flush=True)
def field(model, name):
    return name in env[model]._fields
results = []
required = [
    "motogene_event_activity_hub", "motogene_event_self_service",
    "motogene_event_contacts", "motogene_event_daily_summary",
    "motogene_event_operations_bundle",
]
print("\n=== F06 STAGING READ-ONLY AUDIT ===")
print("Database:", db)
for module in required:
    installed = bool(search("ir.module.module", [
        ("name", "=", module), ("state", "=", "installed")
    ]))
    show("PASS" if installed else "BLOCKER", "MODULE", module + (" installed" if installed else " MISSING"))
if any(level == "BLOCKER" for level, _, _ in results):
    print("HALTED — install/upgrade missing modules before proceeding.")
    raise RuntimeError("F06 prerequisites missing")
website = search("website", [("name", "=", "MOTOGENE GLOBAL")])
if len(website) != 1 or not website.company_id or "morimoto" not in website.company_id.name.lower():
    raise RuntimeError("STOP: ambiguous MotoGene website/company scope")
company = website.company_id
print("SCOPE: website=%s, company=%s" % (website.id, company.id))
scope = [("website_id", "=", website.id), ("company_id", "=", company.id)]
events = search("event.event", scope)
activities = search("motogene.event.activity", scope)
registrations = search("event.registration", [("event_id", "in", events.ids)])
bookings = search("motogene.event.booking", [("event_id", "in", events.ids)])
reviews = search("motogene.event.contact.review", [("event_id", "in", events.ids)])
summaries = search("motogene.event.daily.summary", [
    ("config_id.website_id", "=", website.id),
    ("config_id.company_id", "=", company.id),
])
tokens = search("motogene.event.self.service.token", [("website_id", "=", website.id)])
print("INVENTORY: %s Events, %s Activities, %s Sessions, %s Registrations, "
      "%s Bookings, %s Reviews, %s Summary History, %s Verify Tokens" %
      (len(events), len(activities), len(activities.mapped("session_ids")),
       len(registrations), len(bookings), len(reviews), len(summaries), len(tokens)))
print("REGISTRATION STATUS:", dict(Counter(registrations.mapped("state"))))

# Multi-company / multi-site isolation of all linked records.
wrong_session = activities.mapped("session_ids").filtered(
    lambda s: s.event_id and (
        s.event_id.company_id != company or s.event_id.website_id != website
        or s.event_id.motogene_activity_id != s.activity_id))
wrong_booking = bookings.filtered(
    lambda b: b.company_id != company or b.website_id != website or
    any(r.event_id != b.event_id for r in b.registration_ids))
wrong_review = reviews.filtered(
    lambda r: r.company_id != company or r.registration_id.event_id != r.event_id)
show("BLOCKER" if wrong_session or wrong_booking or wrong_review else "PASS",
     "DATA_SCOPE", "Cross-company / mismatched linked records: Sessions=%s Bookings=%s Reviews=%s" %
     (len(wrong_session), len(wrong_booking), len(wrong_review)))
link_missing = registrations.filtered(
    lambda r: r.motogene_booking_id and r not in r.motogene_booking_id.registration_ids)
show("BLOCKER" if link_missing else "PASS", "BOOKINGS",
     "Booking link inconsistencies: %s" % len(link_missing))
dates_invalid = events.filtered(lambda e: e.date_begin and e.date_end and e.date_end <= e.date_begin)
session_invalid = activities.mapped("session_ids").filtered(
    lambda s: s.date_end and s.date_begin and s.date_end <= s.date_begin)
show("WARN" if dates_invalid or session_invalid else "PASS", "DATE_RANGES",
     "Events with end<=start: %s | Sessions: %s | Event IDs: %s" %
     (len(dates_invalid), len(session_invalid), dates_invalid.ids[:25]))
odd_contacts = registrations.filtered(
    lambda r: r.motogene_contact_sync_status == "off" and r.motogene_attendee_contact_id)
show("WARN" if odd_contacts else "PASS", "CONTACT_LINK",
     "Contact Sync OFF but linked Contacts: %s" % len(odd_contacts))
pending_review = reviews.filtered(lambda r: r.state == "pending")
show("WARN" if pending_review else "PASS", "STAFF_REVIEW",
     "Pending identity reviews: %s" % len(pending_review))

Config = env["motogene.event.daily.config"].sudo()
daily = Config.search([("website_id", "=", website.id),
                       ("company_id", "=", company.id)])
show("PASS" if len(daily) == 1 else "BLOCKER", "DAILY_CONFIG",
     "Daily Summary config count: %s" % len(daily))
contacts_config = search("motogene.event.contact.config", scope)
self_config = search("motogene.event.self.service.config", scope)
show("PASS" if len(contacts_config) == 1 else "WARN", "CONTACT_CONFIG",
     "Contact config: %s | enabled: %s" %
     (len(contacts_config), [c.enabled for c in contacts_config]))
show("PASS" if len(self_config) == 1 else "WARN", "GUEST_CONFIG",
     "Self-Service config: %s | enabled: %s" %
     (len(self_config), [c.enabled for c in self_config]))
cron_ids = (
    ("MAIL_QUEUE", "mail.ir_cron_mail_scheduler_action"),
    ("DAILY_CRON", "motogene_event_daily_summary.ir_cron_motogene_event_daily_summary"),
)
for code, xid in cron_ids:
    cron = env.ref(xid, raise_if_not_found=False)
    if not cron:
        show("BLOCKER", code, "Scheduled action missing")
    else:
        show("WARN" if cron.active else "PASS", code,
             "Active=%s | interval=%s %s | next=%s" %
             (cron.active, cron.interval_number, cron.interval_type, cron.nextcall))
if daily:
    recipients = daily._get_recipients()
    show("WARN" if daily.enabled or daily.send_time_confirmed else "PASS",
         "DAILY_TOGGLE", "Enabled=%s | Approved=%s | recipients=%s" %
         (daily.enabled, daily.send_time_confirmed, len(recipients)))
    show("WARN" if len(recipients) != 1 else "PASS", "RECIPIENTS",
         "Configured Daily Summary recipients: %s (verify authorised test mailbox only)" %
         len(recipients))
server_count = env["ir.mail_server"].sudo().search_count([])
show("PASS" if server_count else "WARN", "SMTP",
     "Outgoing Mail Servers present: %s (delivery verified separately in F05)" % server_count)

outgoing = search("mail.mail", [("state", "=", "outgoing")])
failed_mails = search("mail.mail", [("state", "=", "exception")])
show("WARN" if outgoing else "PASS", "EMAIL_QUEUE",
     "Global pending Outgoing=%s, failed=%s; NEVER run queue during demo if unrelated mail exists" %
     (len(outgoing), len(failed_mails)))
new_enabled = events.filtered(lambda e: e.motogene_ops_notify_new)
cancel_enabled = events.filtered(lambda e: e.motogene_ops_notify_cancel)
owner_enabled = events.filtered(lambda e: e.motogene_ops_booking_confirmation_enabled)
show("WARN" if new_enabled or cancel_enabled or owner_enabled else "PASS", "EVENT_FLAGS",
     "Events with New ToDo=%s, Cancel ToDo=%s, Owner Mail=%s; inspect before demo" %
     (len(new_enabled), len(cancel_enabled), len(owner_enabled)))
question_answers = search("event.registration.answer", [
    ("registration_id", "in", registrations.ids)]) if registrations else env["event.registration.answer"]
show("PASS", "ANSWERS", "Stored custom/native registration answers: %s" % len(question_answers))
show("NOT_TESTED", "SERVER_LOGS",
     "Audit script does not read Odoo.sh build/error logs; review latest staging build and logs manually")
show("NOT_TESTED", "E2E_SECURITY",
     "Review browser role separation, multi-company, token expiry, and penetration cases in final demo")
show("NOT_TESTED", "BACKUP",
     "Verify Odoo.sh staging BACKUP and recovery point before any data deletion")
print("\n=== AUDIT RESULT ===")
for label in ("BLOCKER", "WARN", "PASS", "NOT_TESTED"):
    count = sum(1 for result in results if result[0] == label)
    print("%-10s %d" % (label, count))
print("F06 audit is READ-ONLY. Production signoff requires resolving blockers/warnings and manual checks.")
