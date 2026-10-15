# -*- coding: utf-8 -*-
"""Targeted cleanup of MotoGene shell QA V1 residues, STAGING ONLY.

Deletes ONLY the verified test records and their directly linked messages,
registration emails and attachments. Stops and rolls back on any mismatch.
No cron is enabled and no outbound email is sent.
"""
import os
import re
from unittest.mock import patch

PREFIX = "qa-shell-965ba2aabd"
EVENT_ID = 23
REGISTRATION_IDS = {45, 46}
CONTACT_ID = 23483
TAG_IDS = {42, 43}
CONTACT_EMAIL = PREFIX + "-vip@example.invalid"
EVENT_NAME = PREFIX + " Single Event"
TAG_NAMES = {PREFIX + " Event", PREFIX + " VIP"}

def require(condition, reason):
    if not condition:
        raise RuntimeError("CLEANUP ABORTED: " + reason)

def full_name(value):
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()

db = os.environ.get("PGDATABASE") or env.cr.dbname
require("staging" in db.casefold(), "not on Odoo.sh staging: " + db)

website = env["website"].sudo().search([("name", "=", "MOTOGENE GLOBAL")], limit=1)
require(website and website.company_id, "MotoGene website/company missing")
company = website.company_id
require("morimoto" in company.name.casefold(), "unexpected company")
E = env["res.company"].sudo().with_company(company).env

Partner = E["res.partner"].sudo().with_context(active_test=False)
Event = E["event.event"].sudo().with_context(active_test=False)
Reg = E["event.registration"].sudo().with_context(active_test=False)
Tag = E["res.partner.category"].sudo()
Mail = E["mail.mail"].sudo()
Message = E["mail.message"].sudo()
Attachment = E["ir.attachment"].sudo()
Booking = E["motogene.event.booking"].sudo()
Review = E["motogene.event.contact.review"].sudo()

partner = Partner.browse(CONTACT_ID).exists()
event = Event.browse(EVENT_ID).exists()
registrations = Reg.browse(sorted(REGISTRATION_IDS)).exists()
tags = Tag.browse(sorted(TAG_IDS)).exists()

require(len(partner) == 1 and len(event) == 1, "expected Contact/Event ID not found")
require(set(registrations.ids) == REGISTRATION_IDS, "expected two registration IDs not found")
require(set(tags.ids) == TAG_IDS, "expected two tag IDs not found")

require(partner.name == "QA VIP Existing", "Contact name mismatch")
require((partner.email or "").strip().casefold() == CONTACT_EMAIL, "Contact email mismatch")
require(partner.company_id == company, "Contact belongs to another company")
require(event.name == EVENT_NAME, "Event name mismatch")
require(event.website_id == website and event.company_id == company, "Event scope mismatch")
require(not event.motogene_activity_id and not event.motogene_activity_session_id,
        "Event is linked to an Activity or Session")
require(set(tags.mapped("name")) == TAG_NAMES, "Contact tag labels mismatch")
require(all(full_name(r.name) == "qa vip existing" and
            (r.email or "").strip().casefold() == CONTACT_EMAIL
            and r.event_id == event
            and r.motogene_attendee_contact_id == partner
            and not r.motogene_booking_id
            for r in registrations), "registration identity/ownership mismatch")

all_event_regs = Reg.search([("event_id", "=", EVENT_ID)])
require(set(all_event_regs.ids) == REGISTRATION_IDS,
        "Event has unrelated or additional registrations: " + str(all_event_regs.ids))
tag_holders = Partner.search([("category_id", "in", sorted(TAG_IDS))])
require(set(tag_holders.ids) == {CONTACT_ID},
        "one or both QA tags are assigned to other Contacts: " + str(tag_holders.ids))
require(TAG_IDS.issubset(set(partner.category_id.ids)), "Contact missing QA tags")
require(not Booking.search([("registration_ids", "in", sorted(REGISTRATION_IDS))]),
        "registrations linked to an Event Booking")
require(not Review.search([("registration_id", "in", sorted(REGISTRATION_IDS))]),
        "registrations linked to a Contact Review")

# Do not remove a Contact that has been used in unrelated operational data.
for model, field in (
    ("res.users", "partner_id"),
    ("res.partner.bank", "partner_id"),
    ("sale.order", "partner_id"),
    ("account.move", "partner_id"),
    ("stock.picking", "partner_id"),
    ("crm.lead", "partner_id"),
):
    if model in E.registry.models:
        matched = E[model].sudo().with_context(active_test=False).search(
            [(field, "=", CONTACT_ID)], limit=3)
        require(not matched, "Contact used in %s records %s" % (model, matched.ids))
other_booker = Reg.search([("partner_id", "=", CONTACT_ID), ("id", "not in", sorted(REGISTRATION_IDS))])
other_attendee = Reg.search([
    ("motogene_attendee_contact_id", "=", CONTACT_ID),
    ("id", "not in", sorted(REGISTRATION_IDS)),
])
require(not other_booker and not other_attendee, "Contact linked to registrations outside this QA")
require(not partner.child_ids and not partner.parent_id, "Contact has parent/child hierarchy")

# Only delete outbound mail whose subject identifies this exact dummy Event
# and whose recipients identify only the dummy test email.
mails = Mail.search([("subject", "ilike", EVENT_NAME)])
for mail in mails:
    to = (mail.email_to or "").strip().casefold()
    recipients = [x.strip() for x in re.split(r"[,;]", to) if x.strip()]
    require(recipients and all(x == CONTACT_EMAIL for x in recipients),
            "mail %s has unexpected recipient(s): %s" % (mail.id, to))

messages = (
    Message.search([("model", "=", "event.event"), ("res_id", "=", EVENT_ID)])
    | Message.search([("model", "=", "event.registration"),
                      ("res_id", "in", sorted(REGISTRATION_IDS))])
)
attachments = (
    Attachment.search([("res_model", "=", "event.event"), ("res_id", "=", EVENT_ID)])
    | Attachment.search([("res_model", "=", "event.registration"),
                         ("res_id", "in", sorted(REGISTRATION_IDS))])
)
# Mail attachments outside the target records are not unlinked automatically.
for attachment in mails.mapped("attachment_ids"):
    allowed = (
        (attachment.res_model == "event.event" and attachment.res_id == EVENT_ID)
        or (attachment.res_model == "event.registration"
            and attachment.res_id in REGISTRATION_IDS)
        or (attachment.res_model == "mail.message"
            and attachment.res_id in messages.ids)
    )
    require(allowed or not attachment.res_model,
            "mail attachment %s belongs to unrelated record %s/%s" %
            (attachment.id, attachment.res_model, attachment.res_id))
    attachments |= attachment

print("===== STAGING QA V1 PRE-DELETE CHECKS: PASS =====", flush=True)
print("Database:", db, flush=True)
print("Event:", event.id, event.name, flush=True)
print("Registrations:", registrations.ids, flush=True)
print("Contact:", partner.id, partner.name, partner.email, flush=True)
print("Tags:", [(r.id, r.name) for r in tags], flush=True)
print("Scoped mail.mail:", mails.ids, flush=True)
print("Scoped mail.message:", messages.ids, flush=True)
print("Scoped attachments:", attachments.ids, flush=True)

# Preflight above is read-only. No changes happen unless all checks passed.
# Prevent internal Odoo code from committing halfway through deletion.
Cursor = type(E.cr)
EventMail = type(E["event.mail"])
RegistrationModel = type(Reg)
MailModel = type(Mail)

try:
    with (
        patch.object(Cursor, "commit",
                     side_effect=RuntimeError("Cleanup blocked an unexpected nested commit")),
        patch.object(MailModel, "send_after_commit", autospec=True),
        patch.object(MailModel, "send", autospec=True),
        patch.object(EventMail, "execute", autospec=True),
        patch.object(RegistrationModel, "_update_mail_schedulers", autospec=True),
    ):
        # Delete only scoped email artifacts and records.
        mails.exists().unlink()
        messages.exists().unlink()
        attachments.exists().unlink()
        registrations.exists().unlink()
        event.exists().unlink()
        partner.exists().unlink()
        tags.exists().unlink()
        E.flush_all()
        for name, recordset in (
            ("mail.mail", mails), ("mail.message", messages),
            ("ir.attachment", attachments), ("event.registration", registrations),
            ("event.event", event), ("res.partner", partner),
            ("res.partner.category", tags),
        ):
            require(not recordset.exists(), name + " rows survived unlink")
        require(not Reg.search([("event_id", "=", EVENT_ID)]),
                "some registrations still point to the test event")
    # The only allowed commit: after ALL scoped records have been verified gone.
    E.cr.commit()
    print("CLEANUP SUCCESS — verified and committed ONLY the scoped V1 test rows.",
          flush=True)
except Exception as exc:
    E.cr.rollback()
    print("CLEANUP ROLLED BACK — no deletion committed: %s: %s" %
          (type(exc).__name__, str(exc)), flush=True)
    raise
