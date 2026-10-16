# -*- coding: utf-8 -*-
"""STAGING-ONLY, TWO-PHASE MotoGene Event demo data reset.

DRY RUN unless the operator explicitly supplies the printed RESET token.
Safely scopes MotoGene GLOBAL website, never deletes Contacts, configurations,
SMTP, mail templates, Contact Tags, or other companies' Events.
"""
import os
import hashlib
from collections import Counter

DB = os.environ.get("PGDATABASE") or env.cr.dbname
if "staging" not in DB.lower():
    raise RuntimeError("STOP: cannot reset a non-staging database: %s" % DB)
def get(model, domain):
    return env[model].sudo().with_context(active_test=False).search(domain)
def must(condition, message):
    if not condition:
        raise RuntimeError("RESET BLOCKED: " + message)

websites = get("website", [("name", "=", "MOTOGENE GLOBAL")])
must(len(websites) == 1 and websites.company_id and
     "morimoto" in websites.company_id.name.lower(),
     "Ambiguous website/company. Do not use this script.")
web = websites
company = web.company_id
scope = [("website_id", "=", web.id), ("company_id", "=", company.id)]
events = get("event.event", scope)
activities = get("motogene.event.activity", scope)
sessions = activities.mapped("session_ids")
regs = get("event.registration", [("event_id", "in", events.ids)])
bookings = get("motogene.event.booking", [("event_id", "in", events.ids)])
reviews = get("motogene.event.contact.review", [("event_id", "in", events.ids)])
answers = get("event.registration.answer", [("registration_id", "in", regs.ids)])
schedule_items = get("event.mail.registration", [("registration_id", "in", regs.ids)])
verify_tokens = get("motogene.event.self.service.token", [("website_id", "=", web.id)])
config = get("motogene.event.daily.config", scope)
history = get("motogene.event.daily.summary", [("config_id", "in", config.ids)])
attendee_contacts = regs.mapped("motogene_attendee_contact_id")
all_outgoing = get("mail.mail", [("state", "=", "outgoing")])
scoped_outgoing = env["mail.mail"].sudo().browse()
for mail in all_outgoing:
    linked = (
        mail.model == "event.registration" and mail.res_id in regs.ids or
        mail.model == "event.event" and mail.res_id in events.ids or
        mail.model == "motogene.event.booking" and mail.res_id in bookings.ids
    )
    subject = (mail.subject or "").strip()
    by_subject = any(
        subject in (
            "Your registration at %s" % event.name,
            "MotoGene Booking Confirmation - %s" % event.name,
            "MotoGene Event Cancellation - %s" % event.name,
            "MotoGene - Participant Cancelled - %s" % event.name,
        )
        for event in events
    )
    if linked or by_subject:
        scoped_outgoing |= mail

print("\n=== STAGING MOTOGENE EVENTS RESET — PREVIEW ===")
print("Database:", DB)
print("Company:", company.name, "| website:", web.name)
print("Events:", [(r.id, r.name) for r in events])
print("Activities:", [(r.id, r.name) for r in activities])
print("Sessions:", [(r.id, r.event_id.id) for r in sessions])
print("Registrations:", len(regs), dict(Counter(regs.mapped("state"))))
print("Bookings:", len(bookings), "Reviews:", len(reviews))
print("Registration answers:", len(answers), "Event mail schedule entries:", len(schedule_items))
print("Verification tokens:", len(verify_tokens))
print("Daily Summary HISTORY to reset:", [(r.id, str(r.report_date), r.state) for r in history])
print("Related pending Outgoing emails to CANCEL (not send):", scoped_outgoing.ids)
print("Other Outgoing emails NOT touched:", len(all_outgoing - scoped_outgoing))
print("Attendee Contacts to PRESERVE:", len(attendee_contacts))
print("PRESERVED: SMTP, Contact records/tags, email templates, Daily Summary CONFIG,")
print("          Contact & Self-Service CONFIG, ir.cron settings, sent email audit history.")

# Cross-boundary records must never be removed by side effect.
wrong_session = sessions.filtered(lambda s: s.event_id and s.event_id not in events)
foreign_child = events.filtered(lambda e: e.motogene_activity_id and e.motogene_activity_id not in activities)
wrong_booking = bookings.filtered(
    lambda b: b.website_id != web or b.company_id != company or
    any(r.event_id != b.event_id for r in b.registration_ids))
foreign_bookings = get("motogene.event.booking", [
    ("event_id", "in", events.ids), ("company_id", "!=", company.id)
])
foreign_reviews = get("motogene.event.contact.review", [
    ("event_id", "in", events.ids), ("company_id", "!=", company.id)
])
out_of_scope_regs = get("event.registration", [
    ("motogene_booking_id", "in", bookings.ids), ("event_id", "not in", events.ids)
])
blockers = []
if wrong_session or foreign_child or wrong_booking or foreign_bookings or foreign_reviews or out_of_scope_regs:
    blockers.append("CROSS-WEBSITE/COMPANY LINKS or inconsistent booking references")
if len(config) != 1:
    blockers.append("Expected exactly one Daily Summary config; got %s" % len(config))
if any(e.company_id != company or e.website_id != web for e in events):
    blockers.append("Event website/company mismatch")
if any(a.website_id != web or a.company_id != company for a in activities):
    blockers.append("Activity website/company mismatch")
if any(r.event_id not in events for r in regs):
    blockers.append("Unexpected unrelated registration")
if any(b.event_id not in events for b in bookings):
    blockers.append("Unexpected booking scope")
if any(s.event_id and s.event_id.registration_ids - regs for s in sessions):
    blockers.append("Child Sessions have out-of-scope registrations")

# Paid/sale-linked records are NEVER bulk-deleted.
paid_regs = regs.filtered(lambda r:
    (r.event_ticket_id and "price" in r.event_ticket_id._fields and r.event_ticket_id.price > 0)
    or any(f in r._fields and r[f] for f in ("sale_order_id", "sale_order_line_id")))
paid_events = events.filtered(lambda e: any(
    "price" in ticket._fields and ticket.price > 0 for ticket in e.event_ticket_ids))
if paid_regs or paid_events:
    blockers.append("Paid tickets / sale-linked registrations present: registrations=%s events=%s" %
                    (paid_regs.ids, paid_events.ids))
for model, fld in (("sale.order.line", "event_id"), ("sale.order.line", "event_ticket_id")):
    if model in env.registry.models and fld in env[model]._fields:
        linked_ids = (events.ids if fld == "event_id"
                      else events.mapped("event_ticket_ids").ids)
        if linked_ids:
            sale_count = get(model, [(fld, "in", linked_ids)]).search_count(
                [(fld, "in", linked_ids)])
            if sale_count:
                blockers.append("Existing sale order lines linked by %s: %s" % (fld, sale_count))

queue_cron = env.ref("mail.ir_cron_mail_scheduler_action", raise_if_not_found=False)
daily_cron = env.ref(
    "motogene_event_daily_summary.ir_cron_motogene_event_daily_summary",
    raise_if_not_found=False)
if not queue_cron or not daily_cron:
    blockers.append("Required cron record missing")
elif queue_cron.active or daily_cron.active:
    blockers.append("Turn OFF Email Queue Manager AND MotoGene Daily Summary cron first")
if config and (config.enabled or config.send_time_confirmed):
    blockers.append("Daily Summary Enabled / Send Time Approved must both be OFF")
if any(m.state != "outgoing" for m in scoped_outgoing):
    blockers.append("An email unexpectedly changed state during preflight")

manifest = (
    "web=%s|company=%s|events=%s|activities=%s|sessions=%s|regs=%s|"
    "bookings=%s|reviews=%s|answers=%s|tokens=%s|history=%s|outgoing=%s"
) % (
    web.id, company.id,
    sorted(events.ids), sorted(activities.ids), sorted(sessions.ids),
    sorted(regs.ids), sorted(bookings.ids), sorted(reviews.ids),
    sorted(answers.ids), sorted(verify_tokens.ids), sorted(history.ids),
    sorted(scoped_outgoing.ids),
)
token = "RESET-" + hashlib.sha256(manifest.encode()).hexdigest()[:12].upper()
print("RESET CONFIRM TOKEN:", token)
if blockers:
    print("\n!!! DELETION BLOCKED !!!")
    for reason in blockers:
        print("BLOCKER:", reason)
else:
    print("PREFLIGHT: scope and safety checks PASS.")

confirm = os.environ.get("MOTOGENE_EVENT_RESET_CONFIRM", "").strip()
if not confirm:
    print("\nDRY RUN ONLY — zero writes. Take Odoo.sh staging backup, inspect ALL listed IDs.")
    print("Then run again with MOTOGENE_EVENT_RESET_CONFIRM=%s and BACKUP_ACK=YES." % token)
elif confirm != token:
    raise RuntimeError("STOP: confirmation token does not match CURRENT database inventory.")
else:
    must(os.environ.get("MOTOGENE_RESET_BACKUP_ACK") == "YES",
         "Create/verify staging backup first, then set MOTOGENE_RESET_BACKUP_ACK=YES")
    must(not blockers, "Preflight has blockers: " + " ; ".join(blockers))
    # Transaction-scoped advisory lock: no nested mail/cron commit is allowed.
    from unittest.mock import patch
    CursorClass = type(env.cr)
    RegClass = type(env["event.registration"])
    EventMailClass = type(env["event.mail"])
    MailClass = type(env["mail.mail"])
    try:
        with (
            patch.object(CursorClass, "commit",
                         side_effect=RuntimeError("Unexpected nested DB commit blocked")),
            patch.object(RegClass, "_update_mail_schedulers", autospec=True),
            patch.object(EventMailClass, "execute", autospec=True),
            patch.object(MailClass, "send_after_commit", autospec=True),
            patch.object(MailClass, "send", autospec=True),
        ):
            env.cr.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           ["motogene-demo-reset-%s" % web.id])
            # Prevent deleted Events' unsent notifications leaking into later demo.
            if scoped_outgoing:
                scoped_outgoing.write({"state": "cancel"})
            # Clean links before events, tickets and question records.
            schedule_items.unlink()
            answers.unlink()
            reviews.unlink()
            regs.unlink()
            bookings.unlink()  # ondelete=restrict to event.event
            # Session.unlink automatically deletes its (now empty) native Event.
            sessions.unlink()
            remaining_events = get("event.event", [("id", "in", events.ids)])
            remaining_events.unlink()
            activities.unlink()  # Activity unlink requires no prepared sessions.
            verify_tokens.unlink()
            # Erase report HISTORY only, not the configuration or old Sent mail.
            history.unlink()
            env.flush_all()
            counts = {
                "Events": get("event.event", [("id", "in", events.ids)]).search_count(
                    [("id", "in", events.ids)]),
                "Activities": get("motogene.event.activity", [("id", "in", activities.ids)]).search_count(
                    [("id", "in", activities.ids)]),
                "Registrations": get("event.registration", [("id", "in", regs.ids)]).search_count(
                    [("id", "in", regs.ids)]),
                "Bookings": get("motogene.event.booking", [("id", "in", bookings.ids)]).search_count(
                    [("id", "in", bookings.ids)]),
                "Reviews": get("motogene.event.contact.review", [("id", "in", reviews.ids)]).search_count(
                    [("id", "in", reviews.ids)]),
                "Summary History": get("motogene.event.daily.summary", [("id", "in", history.ids)]).search_count(
                    [("id", "in", history.ids)]),
                "Tokens": get("motogene.event.self.service.token", [("id", "in", verify_tokens.ids)]).search_count(
                    [("id", "in", verify_tokens.ids)]),
            }
            must(not any(counts.values()), "Post-delete data still exists: %s" % counts)
            must(len(get("motogene.event.daily.config", scope)) == 1,
                 "Daily Summary configuration was unexpectedly changed")
            print("PRE-COMMIT VERIFICATION PASS", counts, flush=True)
        env.cr.commit()
        print("SUCCESS: MotoGene GLOBAL staging event DATA RESET committed.", flush=True)
        print("Configurations/Contacts preserved. Old sent mail/attachments retained for traceability.")
    except Exception as exc:
        env.cr.rollback()
        print("RESET ROLLED BACK — no deletion committed: %s: %s" %
              (type(exc).__name__, str(exc)), flush=True)
        raise
