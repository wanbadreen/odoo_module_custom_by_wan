# MotoGene Events: F06 + clean A–Z demo for Jacky (Odoo.sh STAGING)

**Scope:** website MOTOGENE GLOBAL / Morimoto Formulas only. These tools DO NOT change the parent Odoo.sh staging branch or touch production.

## Step 1: F06 before deletion

From Odoo.sh **staging shell**:
- Download QA scripts from custom addons GitHub \`main\` with \`git -C wan_custom_addons fetch origin main\` and \`git -C wan_custom_addons show <sha>:<path> > /tmp/...py\`.
- Run \`python3 -m py_compile\` and \`printf 'exec(compile(open("/tmp/...py").read(), "/tmp/...py", "exec"))\n' | odoo-bin shell --shell-interface python\`.
- Save the COMPLETE F06 audit output. PASS/WARN/BLOCKER is based on current database and config, not a substitute for browser or log review.
- Resolve BLOCKER and review all WARN. Check Odoo.sh latest staging build/logs and BACKUPS manually. Do not mark M14 finished just from a static script.

## Step 2: Reset preview (NO data deleted)

Run \`qa/staging_reset_for_demo.py\` without environment variables.

The script shows exact Event/Activity IDs, bookings, registrations, custom registration answers, staff reviews, email verification tokens, summary HISTORY, queued emails belonging to these Events, and a reset token. **It does not delete Contacts, VIP flags/tags or any settings**.

If it lists a real event or sales-linked record, do NOT proceed. The script blocks paid tickets, sale-linked registrations and cross-company/session links. It never selects other websites/companies' Events.

## Step 3: Backup + reset with explicit token

1. Confirm **Odoo.sh staging backup / restore point** exists and is recoverable.
2. Ensure BOTH scheduled actions are OFF:
   - \`Mail: Email Queue Manager\`
   - \`MotoGene: Event Daily Registration Summary\`
3. Keep Daily Summary **Enabled OFF**, **Send Time Approved OFF**.
4. Review preview inventory and share it for approval before deleting.
5. On an approved second run, set \`MOTOGENE_RESET_BACKUP_ACK=YES\`
   and \`MOTOGENE_EVENT_RESET_CONFIRM=RESET-XXXXXXXXXXXX\` (exactly the token emitted in preview).
6. Script cancels only queued email linked to the scoped Events, deletes event registrations/answers, reviews, bookings, linked Sessions/Events/Activities, verification tokens, and TEST Daily Summary **history**. It verifies no target records remain and commits once.
7. Existing **sent emails and email attachments are retained for traceability**, so this is NOT a global mail history wipe.
8. If anything fails before commit, the deletion transaction is rolled back. Do not bypass the guard in SQL.

**Intentionally preserved:** Contacts (even auto-created event attendees), Contact Tags, Event templates, Event email templates, Outlook SMTP, Studio settings, Daily Summary config and recipients, Contacts config, Self-Service config, cron definitions, other companies/website's Events. Previously-created demo contacts may still match future registrations; use fresh test emails/names.

## Step 4: Demo toggle matrix (staging)

| Area | Before/demo setup | After demo |
|---|---|---|
| Outlook SMTP server | Keep configured; test mailbox only | Preserve |
| Mail: Email Queue Manager | **OFF**; never process whole queue | OFF |
| MotoGene Daily Summary cron | **OFF** | OFF |
| Daily Summary Enabled / Time Approved | OFF / OFF; manual one-email only | OFF / OFF |
| Daily Summary recipient | Only authorised test mailbox | Preserve approved config |
| Contacts Integration config | ON for MORIMOTO website to demo match/review | Existing setting per policy |
| Guest Self-Service config | ON to demo token/email verification | Existing setting per policy |
| Event > Enable Attendee Contacts | ON only demo Event; Activity ON for child sync | Demo records may be archived |
| Event > Booking Owner Confirmation | OFF by default; ON **only** during controlled test | OFF |
| Event > New/Cancel To Do | ON for test Event with named internal assignee | OFF |
| Event > Staff Notification Contacts | Only approved test Contact | Clear from demo Event |
| Event > Communication (After each registration) | Set native confirmation template; direct queues may stay OUTGOING with global queue OFF | Do not turn on global queue |
| Production | NEVER touched | Requires M15 approval |

## Step 5: Demo script — Jacky, from A to Z

1. **Configuration tour:** choose Morimoto company, MOTOGENE GLOBAL website, show daily/email config and disabled bulk schedulers. Show code/build version and Events menus.
2. **Single Event:** create a future-dated FREE Event with real start < end; venue, description, two attendance questions; publish; see it on the website.
3. **Activity + Sessions:** create Activity with two future Sessions, different capacities/time slots; centralized Questions incl Choice/Answers; Prepare Sessions and Publish. Verify Session events not duplicated on /event.
4. **Group Booking:** Incognito website registration for three participants: booking owner email test account, participant A own email, participant B different email, participant C no personal email. Verify capacity, QR/native confirmation structure.
5. **Contacts:** one new attendee Contact created/tagged; a matching re-registration links without duplicates; name mismatch raises Contact Review, unresolved until staff chooses existing/separate Contact. No existing VIP Contact is overwritten.
6. **Operations notifications:** Event To Do for new registration and Cancellation; check assignee and no duplicates. Staff recipient receives Contact Review alert when appropriate.
7. **Email:** one Owner Booking Confirmation contains all participants; native attendee confirmation remains outgoing unless sent individually or a safe queue policy is approved; verify recipients against test-only addresses before any send. Self-Service verification and cancellation messages use post-commit direct sends.
8. **Manage My Registration:** booker email verification grants access to all participants in its booking; each attendee email verifies own only; no-email attendee managed by booker only. Cancel one attendee, ensure other registrations stay registered and seat is restored.
9. **Exports:** Excel from Single Event and Activity. Check two sheets, custom answers, booking vs participant email, attendee status, no duplicate rows, correct dates.
10. **Daily Summary:** keep auto cron/config OFF; generate one report only for test date with one authorised recipient using a guarded single-mail process. Check Excel + email counts, history, duplicate prevention.
11. **Security:** check guest cannot access other company/booking/registration, staff permissions, company separation, invalid tokens, cancellations after event date, sold-out limitation.
12. **Handover:** archive/unpublish demo Events (or run approved reset after backup), preserve configs and record all findings/sign-off.

## Outstanding closure conditions

F01/F02 findings: confirm Single Event end time really later than start, and investigate unexpected Contact display for some Session registrations. Verify pending identity reviews and any email OUTGOING from the demo. The shell reports are **not** proof that end-to-end web, production rollback, or live customer email delivery is secure.

M14 is only **PASS** after F06 evidence + browser smoke/regression + manual review of Odoo.sh logs and backup. M15 production promotion remains a separate explicitly approved action.
