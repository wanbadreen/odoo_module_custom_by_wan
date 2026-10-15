# -*- coding: utf-8 -*-
"""One-shot QA of Combined MotoGene Operations release. Staging only.

No native event mailing, SMTP sending, or database commit is allowed.
All temporary records and settings are rolled back; no production access.
"""
import os
import io
import uuid
import zipfile
from datetime import timedelta
from unittest.mock import patch

from odoo import fields

db = os.environ.get("PGDATABASE") or env.cr.dbname
if "staging" not in db.casefold():
    raise RuntimeError("STOP: not a staging DB (%s)" % db)
for module in (
    "motogene_event_operations_bundle",
    "motogene_event_contacts",
    "motogene_event_daily_summary",
    "motogene_event_activity_hub",
):
    if not env["ir.module.module"].sudo().search([
        ("name", "=", module), ("state", "=", "installed")
    ], limit=1):
        raise RuntimeError("STOP: required module not installed: " + module)

website = env["website"].sudo().search([("name", "=", "MOTOGENE GLOBAL")], limit=1)
if not website or not website.company_id or "morimoto" not in website.company_id.name.casefold():
    raise RuntimeError("STOP: company / website mismatch")
E = env["res.company"].sudo().with_company(website.company_id).env
prefix = "qa-ops-" + uuid.uuid4().hex[:10]
Mail = type(E["mail.mail"])
RegClass = type(E["event.registration"])
Cursor = type(E.cr)
EventMail = type(E["event.mail"])
results = []
shared = {}

def case(label, fn, requires=()):
    missing = [item for item in requires if item not in shared]
    if missing:
        results.append((label, "SKIP", "Prerequisite missing: " + ", ".join(missing)))
        print("SKIP " + label, flush=True)
        return
    try:
        with E.cr.savepoint():
            fn()
        results.append((label, "PASS", ""))
        print("PASS " + label, flush=True)
    except Exception as exc:
        msg = "%s: %s" % (type(exc).__name__, str(exc)[:260])
        results.append((label, "FAIL", msg))
        print("FAIL %s: %s" % (label, msg), flush=True)

try:
    with (
        patch.object(Cursor, "commit", side_effect=RuntimeError("COMMIT blocked by QA")),
        patch.object(Mail, "send", autospec=True),
        patch.object(Mail, "send_after_commit", autospec=True),
        patch.object(RegClass, "_update_mail_schedulers", autospec=True),
        patch.object(EventMail, "execute", autospec=True),
    ):
        start = fields.Datetime.to_datetime(fields.Datetime.now()) + timedelta(days=10)
        event = E["event.event"].create({
            "name": prefix + " Single Event",
            "website_id": website.id,
            "company_id": website.company_id.id,
            "date_begin": start, "date_end": start + timedelta(hours=2),
        })
        contact = E["res.partner"].create({
            "name": prefix + " Staff", "email": prefix + "@example.invalid",
        })
        def reg(name, email=False, ev=None):
            return E["event.registration"].create({
                "event_id": (ev or event).id, "partner_id": False,
                "name": name, "email": email or False,
            })

        def c01():
            w = E["motogene.event.export.wizard"].create({
                "scope": "event", "event_id": event.id,
            })
            before = E["mail.mail"].search_count([])
            r = reg("QA Manual Export", prefix + "-export@example.invalid")
            act = w.action_export_xlsx()
            assert act["type"] == "ir.actions.act_url" and w.file_data
            assert E["mail.mail"].search_count([]) == before
            assert r.event_id == event
        case("OPS01 manual Excel no email", c01)

        def c02():
            r = reg("QA Formula", prefix + "-formula@example.invalid")
            q = E["event.question"].create({
                "event_id": event.id, "title": "Favourite Activity",
                "question_type": "simple_choice",
            })
            a = E["event.question.answer"].create({
                "question_id": q.id, "name": "=EVAL(1)",
            })
            E["event.registration.answer"].create({
                "registration_id": r.id, "question_id": q.id,
                "value_answer_id": a.id,
            })
            config = E["motogene.event.daily.config"].new({"timezone": "Asia/Kuala_Lumpur"})
            z = zipfile.ZipFile(io.BytesIO(config._build_xlsx(r, r)))
            assert "Favourite Activity" in z.read("xl/sharedStrings.xml").decode("utf-8")
            assert "=EVAL(1)" in z.read("xl/sharedStrings.xml").decode("utf-8")
            assert "<f>" not in z.read("xl/worksheets/sheet1.xml").decode("utf-8")
            assert "Booking Email" in z.read("xl/sharedStrings.xml").decode("utf-8")
        case("OPS02 custom Questions & formula-safe Excel", c02)

        def c03():
            r = reg("QA Default Off", prefix + "-off@example.invalid")
            assert not r.activity_ids.filtered(lambda a: (a.summary or "").startswith("MotoGene Event —"))
        case("OPS03 staff To Do off by default", c03)

        def c04():
            event.write({
                "motogene_ops_notify_new": True,
                "motogene_ops_activity_user_id": E.user.id,
            })
            r = reg("QA To Do New", prefix + "-new@example.invalid")
            assert len(r.activity_ids.filtered(lambda a: a.summary == "MotoGene Event — New Registration")) == 1
            r._motogene_staff_activity("new")
            assert len(r.activity_ids.filtered(lambda a: a.summary == "MotoGene Event — New Registration")) == 1
            shared["reg"] = r
        case("OPS04 opt-in new registration To Do + dedupe", c04)

        def c05():
            event.write({"motogene_ops_notify_cancel": True})
            r = shared["reg"]
            r.action_cancel()
            assert r.state == "cancel"
            assert len(r.activity_ids.filtered(lambda a: a.summary == "MotoGene Event — Cancellation")) == 1
        case("OPS05 opt-in cancellation To Do", c05, ("reg",))

        def c06():
            event.motogene_ops_staff_ids = [(4, contact.id)]
            config = E["motogene.event.self_service.config"].search([
                ("website_id", "=", website.id), ("company_id", "=", website.company_id.id),
            ], limit=1)
            if not config:
                config = E["motogene.event.self_service.config"].create({
                    "name": prefix + " Config",
                    "website_id": website.id, "company_id": website.company_id.id,
                })
            assert contact.email in config._staff_emails(event)
        case("OPS06 existing cancellation recipients union", c06)

        def c07():
            begin = start + timedelta(days=4)
            activity = E["motogene.event.activity"].create({
                "name": prefix + " Activity",
                "website_id": website.id, "company_id": website.company_id.id,
                "notification_partner_ids": [(4, contact.id)],
                "session_ids": [(0, 0, {
                    "name": "AM", "date_begin": begin,
                    "date_end": begin + timedelta(hours=2), "seats_max": 12,
                })],
            })
            activity.action_prepare_sessions()
            child = activity.session_ids.mapped("event_id")
            assert len(child) == 1 and contact in child.motogene_ops_staff_ids
            shared["activity"] = activity
        case("OPS07 Activity staff recipients sync to child", c07)

        def c08():
            count = E["mail.mail"].search_count([])
            info = event.motogene_ops_email_audit
            assert "Native Odoo" in info and "without individual email" in info
            assert event.action_motogene_ops_email_check()["type"] == "ir.actions.client"
            assert E["mail.mail"].search_count([]) == count
        case("OPS08 email readiness is read-only", c08)


        def c09():
            event.write({
                "motogene_contacts_enabled": True,
                "motogene_ops_booking_confirmation_enabled": True,
            })
            r1 = reg("QA Booker", prefix + "-booker@example.invalid")
            r2 = reg("QA No Individual Email")
            booking = E["motogene.event.booking"].create({
                "event_id": event.id,
                "company_id": website.company_id.id,
                "website_id": website.id,
                "owner_email": prefix + "-owner@example.invalid",
            })
            (r1 | r2).write({"motogene_booking_id": booking.id})
            before = E["mail.mail"].search_count([])
            booking.action_queue_owner_confirmation()
            booking.action_queue_owner_confirmation()
            mail = booking.motogene_ops_confirmation_mail_id
            assert mail and mail.email_to == prefix + "-owner@example.invalid"
            assert E["mail.mail"].search_count([]) == before + 1
            assert "QA Booker" in mail.body_html
            assert "QA No Individual Email" in mail.body_html
        case("OPS09 booking owner mail opt-in + idempotency", c09)

finally:
    E.cr.rollback()
    # ORM is mandatory for JSONB translated names in Odoo 18.
    residual = {
        "Events": E["event.event"].sudo().search_count([("name", "=ilike", prefix + "%")]),
        "Activities": E["motogene.event.activity"].sudo().search_count([("name", "=ilike", prefix + "%")]),
        "Contacts": E["res.partner"].sudo().search_count([("email", "=ilike", prefix + "%")]),
        "Registrations": E["event.registration"].sudo().search_count([("email", "=ilike", prefix + "%")]),
    }
    print("ROLLBACK AUDIT: " + str(residual), flush=True)
    if any(residual.values()):
        print("WARNING: QA artifacts found; inspect before any cleanup.", flush=True)
    else:
        print("ROLLBACK VERIFIED — no QA artifacts.", flush=True)

count = sum(status == "PASS" for _, status, _ in results)
print("\n===== M11-M14 OPERATIONS QA: %s/%s PASS =====" % (count, len(results)))
for name, state, message in results:
    print("%s | %s %s" % (state, name, message))
print("Excluded: live SMTP delivery, browser interaction, and production deploy.")
