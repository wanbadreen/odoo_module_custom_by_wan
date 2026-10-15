# -*- coding: utf-8 -*-
"""Targeted cleanup of MotoGene shell QA V1 residues, STAGING ONLY.

Deletes ONLY the verified test records and their directly linked messages,
registration emails and attachments. Stops and rolls back on any mismatch.
No cron is enabled and no outbound email is sent.
"""
import os
import re
from email.utils import getaddresses
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
# The read-only audit confirmed the partially committed original V1 test:
#   ID 45: exact/lowercase email, Attendee Contact 23483, Sync 'matched'.
#   ID 46: extra spaces/uppercase email, NO Attendee Contact, Sync 'off'.
# These are two KNOWN states, not a reason to relax other identity checks.
for registration_id, expected_contact_id, expected_sync in (
    (45, CONTACT_ID, "matched"),
    (46, False, "off"),
):
    r = registrations.filtered(lambda x: x.id == registration_id)
    require(len(r) == 1, "registration %s missing" % registration_id)
    require(
        full_name(r.name) == "qa vip existing"
        and (r.email or "").strip().casefold() == CONTACT_EMAIL
        and r.event_id == event
        and r.motogene_attendee_contact_id.id == expected_contact_id
        and r.motogene_contact_sync_status == expected_sync
        and not r.partner_id
        and not r.motogene_booking_id
        and r.state == "open",
        "registration %s identity/ownership/status mismatch" % registration_id,
    )

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
# and whose To/Cc/partner recipients identify ONLY the dummy test contact.
# Odoo stores RFC 5322 formatted To addresses, e.g.
# '"QA VIP Existing" <qa-shell-...-vip@example.invalid>'.
# Never split on commas without parsing the mailbox/display-name format.
mails = Mail.search([("subject", "ilike", EVENT_NAME)])
for mail in mails:
    recipient_strings = [mail.email_to or "", mail.email_cc or ""]
    parsed_to = getaddresses([recipient_strings[0]]) if recipient_strings[0].strip() else []
    parsed_cc = getaddresses([recipient_strings[1]]) if recipient_strings[1].strip() else []
    addresses = [(email or "").strip().casefold() for _, email in (parsed_to + parsed_cc)]
    require(bool(addresses) or bool(mail.recipient_ids),
            "mail %s has no identifiable recipient" % mail.id)
    require(all(address == CONTACT_EMAIL for address in addresses),
            "mail %s has unexpected To/Cc address(es): %s" %
            (mail.id, addresses))
    require(all(p.id == CONTACT_ID and (p.email or "").strip().casefold() == CONTACT_EMAIL
                for p in mail.recipient_ids),
            "mail %s has unexpected linked recipient Contact IDs: %s" %
            (mail.id, mail.recipient_ids.ids))

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
