# -*- coding: utf-8 -*-
"""One-shot Odoo.sh STAGING backend QA; all writes rolled back; no outgoing emails."""
import os
import uuid
from datetime import timedelta
from unittest.mock import patch
from odoo import fields
from odoo.exceptions import AccessError, UserError

DB = os.environ.get("PGDATABASE", "") or env.cr.dbname
if "staging" not in DB.lower():
    raise RuntimeError("STOP: this is not a staging database: %s" % DB)
for name in ("motogene_event_contacts", "motogene_event_self_service", "motogene_event_activity_hub"):
    if not env["ir.module.module"].sudo().search([("name", "=", name), ("state", "=", "installed")], limit=1):
        raise RuntimeError("STOP: required module not installed: %s" % name)
site = env["website"].sudo().search([("name", "=", "MOTOGENE GLOBAL")], limit=1)
if not site or not site.company_id or "morimoto" not in site.company_id.name.lower():
    raise RuntimeError("STOP: Morimoto website not found")
company = site.company_id
E = env["res.company"].sudo().with_company(company).env
prefix = "qa-shell-" + uuid.uuid4().hex[:10]
results, shared = [], {}

# Audit possible artifacts from the previous runner. That older runner allowed
# native Odoo Event Mail to COMMIT while inside a savepoint, so its final
# rollback cannot prove that all earlier dummy rows were removed.
previous_partners = E["res.partner"].sudo().search([
    ("email", "ilike", "qa-shell-%@example.invalid"),
], limit=15)
previous_events = E["event.event"].sudo().search([
    ("name", "ilike", "qa-shell-%"),
], limit=15)
previous_tags = E["res.partner.category"].sudo().search([
    ("name", "ilike", "qa-shell-%"),
], limit=15)
if previous_partners or previous_events or previous_tags:
    print("WARNING: Possible artifacts from the PREVIOUS QA run (nothing deleted):", flush=True)
    print("  Contact IDs:", previous_partners.ids, flush=True)
    print("  Event IDs:", previous_events.ids, flush=True)
    print("  Tag IDs:", previous_tags.ids, flush=True)
else:
    print("PREVIOUS QA ARTIFACT AUDIT: no matching Contacts, Events or Tags.", flush=True)

def check(label, func, requires=()):
    missing = [key for key in requires if key not in shared]
    if missing:
        msg = "Prerequisite case failed: " + ", ".join(missing)
        results.append((label, "SKIP", msg))
        print("SKIP %s: %s" % (label, msg), flush=True)
        return
    try:
        with E.cr.savepoint():
            func()
        results.append((label, "PASS", ""))
        print("PASS " + label, flush=True)
    except Exception as exc:
        msg = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        results.append((label, "FAIL", msg))
        print("FAIL %s: %s" % (label, msg), flush=True)

try:
    # Native Odoo 18 event.registration.create() immediately executes Event
    # Mail schedulers. Those may render PDFs and COMMIT the current cursor.
    # Patching mail.mail alone does NOT stop that commit. Disable only this
    # shell's automatic Event Mail processing, and reject any unexpected commit.
    # Patches are restored at the end of the with-block.
    Mail = type(E["mail.mail"])
    Registration = type(E["event.registration"])
    EventMail = type(E["event.mail"])
    Cursor = type(E.cr)
    with (
        patch.object(Mail, "send_after_commit", autospec=True),
        patch.object(Mail, "send", autospec=True),
        patch.object(Registration, "_update_mail_schedulers", autospec=True),
        patch.object(EventMail, "execute", autospec=True),
        patch.object(Cursor, "commit", side_effect=RuntimeError(
            "QA SAFETY: unexpected database COMMIT blocked"
        )),
    ):
        conf = E["motogene.event.contact.config"].search([
            ("website_id", "=", site.id), ("company_id", "=", company.id)
        ], limit=1)
        if not conf:
            conf = E["motogene.event.contact.config"].create({
                "name": "Temporary Shell QA", "website_id": site.id,
                "company_id": company.id, "enabled": True,
            })
        conf.enabled = True  # transaction-only override
        service = E["motogene.event.self.service.config"].search([
            ("website_id", "=", site.id), ("company_id", "=", company.id)
        ], limit=1)
        if not service:
            service = E["motogene.event.self.service.config"].create({
                "name": "Temporary Shell QA", "website_id": site.id,
                "company_id": company.id, "enabled": True,
            })
        tag = E["res.partner.category"].create({"name": prefix + " Event"})
        vip = E["res.partner.category"].create({"name": prefix + " VIP"})
        future = fields.Datetime.to_datetime(fields.Datetime.now()) + timedelta(days=12)
        event = E["event.event"].create({
            "name": prefix + " Single Event", "website_id": site.id,
            "company_id": company.id, "date_begin": future,
            "date_end": future + timedelta(hours=2),
            "motogene_contacts_enabled": True,
            "motogene_contact_tag_ids": [(6, 0, tag.ids)],
        })

        def reg(name, email=False, ev=None):
            return E["event.registration"].create({
                "event_id": (ev or event).id, "name": name,
                "email": email or False, "partner_id": False,
            })

        def vip_tags():
            address = prefix + "-vip@example.invalid"
            p = E["res.partner"].create({
                "name": "QA VIP Existing", "email": address,
                "company_id": company.id, "category_id": [(4, vip.id)],
            })
            r1 = reg("QA VIP Existing", address)
            r2 = reg(" QA  VIP   Existing ", address.upper())
            assert r1.motogene_contact_sync_status == "matched"
            assert r2.motogene_attendee_contact_id == p
            assert vip in p.category_id and tag in p.category_id
            assert E["res.partner"].search_count([("email_normalized", "=", address)]) == 1
        check("C11 VIP tags additive + repeat match", vip_tags)

        def review_create():
            address = prefix + "-review@example.invalid"
            original = E["res.partner"].create({
                "name": "QA Original Name", "email": address, "company_id": company.id
            })
            r = reg("QA New Different Name", address)
            v = r.motogene_contact_review_ids
            assert len(v) == 1 and v.reason == "name_mismatch" and v.state == "pending"
            assert original in v.candidate_ids and r.motogene_contact_sync_status == "review"
            v.resolution_note = "Confirmed distinct person in shell QA"
            v.action_create_separate()
            assert v.state == "resolved" and r.motogene_contact_sync_status == "created"
            assert r.motogene_attendee_contact_id != original
            assert original.name == "QA Original Name"
        check("C12 mismatch -> manual Create Separate", review_create)

        def multiple_candidates():
            address = prefix + "-twins@example.invalid"
            for word in ("One", "Two"):
                E["res.partner"].create({
                    "name": "QA Twin " + word, "email": address, "company_id": company.id
                })
            r = reg("QA Twin One", address)
            v = r.motogene_contact_review_ids
            assert r.motogene_contact_sync_status == "review"
            assert v.reason == "multiple_contacts" and len(v.candidate_ids) == 2
            assert not r.motogene_attendee_contact_id
        check("C13 multiple Contacts cannot auto-match", multiple_candidates)

        def booking_access():
            owner = prefix + "-owner@example.invalid"
            farah = reg("QA Farah", prefix + "-farah@example.invalid")
            aina = reg("QA Aina", prefix + "-aina@example.invalid")
            riena = reg("QA Riena")
            booking = E["motogene.event.booking"].create({
                "owner_email": owner, "event_id": event.id,
                "company_id": company.id, "website_id": site.id,
            })
            (farah | aina | riena).write({"motogene_booking_id": booking.id})
            assert riena.motogene_contact_sync_status == "no_email"
            assert not riena.motogene_attendee_contact_id
            assert set(service._registrations_for_email(owner).ids) == set(booking.registration_ids.ids)
            assert service._motogene_can_manage_registration(riena, owner)
            assert service._motogene_can_manage_registration(aina, aina.email)
            assert not service._motogene_can_manage_registration(riena, aina.email)
            assert not service._motogene_can_manage_registration(farah, aina.email)
            shared.update({"owner": owner, "farah": farah, "aina": aina, "riena": riena})
        check("C14 booking owner all, attendee own only", booking_access)

        def wrong_cancellation():
            r = shared["riena"]
            try:
                service._cancel_for_verified_email(r.id, shared["aina"].email)
            except AccessError:
                pass
            else:
                raise AssertionError("Unauthorized cancellation allowed")
            assert r.state == "open"
        check("C15 cross-attendee cancellation denied", wrong_cancellation, requires=("riena", "aina"))

        def owner_cancellation():
            service._cancel_for_verified_email(shared["riena"].id, shared["owner"])
            assert shared["riena"].state == "cancel"
            assert shared["farah"].state == "open" and shared["aina"].state == "open"
        check("C16 booker cancels only selected attendee", owner_cancellation, requires=("riena", "farah", "aina", "owner"))

        def own_cancellation():
            service._cancel_for_verified_email(shared["aina"].id, shared["aina"].email)
            assert shared["aina"].state == "cancel" and shared["farah"].state == "open"
        check("C17 attendee email cancels only own", own_cancellation, requires=("aina", "farah"))

        def disabled_integration():
            conf.enabled = False
            r = reg("QA Off", prefix + "-off@example.invalid")
            assert r.motogene_contact_sync_status == "off"
            assert not r.motogene_attendee_contact_id
            conf.enabled = True
        check("C18 disabled config has no Contact writes", disabled_integration)

        def multiple_sessions():
            activity = E["motogene.event.activity"].create({
                "name": prefix + " Activity", "company_id": company.id,
                "website_id": site.id, "motogene_contacts_enabled": True,
                "motogene_contact_tag_ids": [(6, 0, tag.ids)],
                "session_ids": [
                    (0, 0, {"name": "A", "date_begin": future + timedelta(days=1),
                             "date_end": future + timedelta(days=1, hours=2), "seats_max": 10}),
                    (0, 0, {"name": "B", "date_begin": future + timedelta(days=2),
                             "date_end": future + timedelta(days=2, hours=2), "seats_max": 10}),
                ],
            })
            activity.action_prepare_sessions()
            events = activity.session_ids.mapped("event_id")
            assert len(events) == 2
            assert all(ev.motogene_contacts_enabled and tag in ev.motogene_contact_tag_ids for ev in events)
            assert all(ev.question_ids.filtered(lambda q: q.question_type == "name") for ev in events)
            r = reg("QA Child", prefix + "-child@example.invalid", events[0])
            assert r.motogene_contact_sync_status == "created"
            shared.update({"activity": activity, "child": events[0]})
        check("C19 activity -> 2 sessions + Questions + Contact tags", multiple_sessions)

        def historical_answers():
            activity, child = shared["activity"], shared["child"]
            source = E["event.question"].create({
                "title": "QA Preferred Channel", "question_type": "simple_choice",
                "motogene_activity_id": activity.id,
            })
            answer = E["event.question.answer"].create({
                "name": "Facebook", "question_id": source.id,
            })
            activity._sync_questions(child)
            question = child.question_ids.filtered(lambda q: q.motogene_activity_source_id == source)
            option = question.answer_ids.filtered(lambda a: a.motogene_activity_source_answer_id == answer)
            assert len(question) == 1 and len(option) == 1
            r = reg("QA Historical", prefix + "-hist@example.invalid", child)
            E["event.registration.answer"].create({
                "registration_id": r.id, "question_id": question.id,
                "value_answer_id": option.id,
            })
            for protected in (answer, option):
                try:
                    protected.write({"name": "TikTok"})
                except UserError:
                    pass
                else:
                    raise AssertionError("Used answer was renamed")
            assert option.name == "Facebook"
            unused = E["event.question.answer"].create({
                "name": "Instagram", "question_id": source.id,
            })
            unused.name = "WhatsApp"
            activity._sync_questions(child)
            assert "WhatsApp" in question.answer_ids.mapped("name")
        check("C20 used-answer label immutable; unused option editable", historical_answers, requires=("activity", "child"))
finally:
    # This time all event email schedulers AND cursor.commit are blocked.
    # Roll back regardless of failed tests or errors in test setup.
    E.cr.rollback()
    # Odoo 18 stores translated Event/Activity names in JSONB; raw SQL
    # ILIKE on that column raises UndefinedFunction. ORM ilike is correct.
    # This verification is separate from test PASS/FAIL and is read-only.
    try:
        audit_models = {
            "Contacts": ("res.partner", "email"),
            "Events": ("event.event", "name"),
            "Activities": ("motogene.event.activity", "name"),
            "Tags": ("res.partner.category", "name"),
            "Bookings": ("motogene.event.booking", "owner_email"),
            "Registrations by Email": ("event.registration", "email"),
            "Registrations by Name": ("event.registration", "name"),
        }
        remaining = {
            label: E[model].sudo().search_count([(field, "=ilike", prefix + "%")])
            for label, (model, field) in audit_models.items()
        }
        if any(remaining.values()):
            print("WARNING: QA rollback audit found new rows: %s" % remaining, flush=True)
        else:
            print("\nROLLBACK VERIFIED: no Contacts, Events, Activities, Tags, "
                  "Bookings or Registrations from this QA run remain.", flush=True)
    except Exception as exc:
        print("ROLLBACK AUDIT ERROR: %s: %s" % (type(exc).__name__, exc), flush=True)

passed = sum(status == "PASS" for _, status, _ in results)
skipped = sum(status == "SKIP" for _, status, _ in results)
failed = sum(status == "FAIL" for _, status, _ in results)
print("\n===== MotoGene Backend QA: %s/%s PASS, %s FAIL, %s SKIP =====" % (
    passed, len(results), failed, skipped,
))
for label, status, issue in results:
    print("%s | %s %s" % (status, label, issue))
print("Scope: backend logic only; website UI and actual email delivery not tested.")
