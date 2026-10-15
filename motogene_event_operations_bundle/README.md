# MotoGene Events — Combined Operations Release (M11–M14)

Release module: `motogene_event_operations_bundle` (Odoo 18).

## Dependencies
- `motogene_event_daily_summary` (existing deduplicated two-sheet XLSX + history + disabled cron)
- `motogene_event_activity_hub`
- `motogene_event_contacts`
- `motogene_event_self_service`

This single addon does not replace or uninstall any existing Events addons.

## Scope
**M11 Reports:** Events > Attendee Excel Export, Event form > Export Attendees,
and Activity > Export Attendees. Both sheets now contain Activity, Session,
Booking Email, attendee Contact/Sync and custom Selection/Text Box answers.
Existing Daily Summary email uses the same extended XLSX builder.

**M12 Staff:** Event > MotoGene Operations, staff Contacts and an optional
assigned Odoo To Do for new registration / cancellation. Activity staff
recipients are inherited by child Sessions on Prepare / Sync Sessions.
Both automatic To Do toggles default OFF.

**M13 Email:** Native Odoo event.mail templates/scheduling are left intact.
A read-only Email Readiness diagnostic shows native template count, no-email
attendee count, staff Contacts and opt-in settings. Optional ONE booking-owner
confirmation email can be enabled per Event/Activity, including a summary of
attendees without personal email. This is OFF by default. Existing native
per-attendee confirmations and guest self-service are not replaced.
No SMTP settings, global mail queues or cron are enabled by the new addon.

**M14 QA:** `qa/one_shot_staging.py`, 9 rollback-only backend tests. Native
registration email scheduler, SMTP sending and transaction commit are
blocked in that runner. `tests/test_operations_bundle.py` contains
10 Odoo automated regression tests for this addon (not executed on push).

## Staging installation / smoke tests
1. Update custom addon submodule pointer ONLY on parent `staging`, never
   production, and wait for a green Odoo.sh build.
2. Update Apps List; install MotoGene Events Operations Bundle (the Daily
   Summary dependency is installed automatically if missing).
3. Verify Daily Summary scheduled action remains inactive and its Settings
   remain disabled. Do not approve an invented send time.
4. Verify Event/Activity Excel exports using sandbox test participants,
   including custom Questions, cancelled registrations and empty emails.
5. Verify staff To Do OFF by default; enable temporarily on one dummy
   Event and test new/cancel Odoo activity once.
6. Verify booking-owner email OFF by default, then test it with staging
   neutralisation and the booking email queue; confirm one mail per booking.
7. Run `qa/one_shot_staging.py` in the STAGING Odoo shell; inspect full logs.
8. Verify native attendee email template, sender, SMTP route, booking owner
   delivery, and cancellation verification via controlled end-to-end staging
   testing. Do NOT consider live email delivered based on queue status alone.

## Production readiness gate (M15; not a deployment)
- M14 automated + browser QA, role isolation, cross-website/company access,
  empty participant-email behavior, duplicated submissions and historical
  Questions data protection must all be verified.
- Obtain signoff on recipient lists, approved send time, and explicit mail
  sending policy before activating any cron or booking-owner confirmations.
- Review all third-party custom addons, system logs, outgoing mail effects,
  a backup and rollback plan before any future production promotion.
- **This release does not deploy to production.**
