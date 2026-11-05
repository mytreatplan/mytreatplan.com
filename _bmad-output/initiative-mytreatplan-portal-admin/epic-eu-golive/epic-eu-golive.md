---
type: epic
title: "EU production go-live"
parent: initiative-mytreatplan-portal-admin
covers: [P14]
after: []
assignee: ""
risk: high
---

# EU production go-live

## Description

Puts the portal (mytreatplan.com, with mytreatplan.es redirecting) and the admin site (admin.mytpdso.com, on its own instance) into EU production, running part-1's held production stories (1.7, 1.12, 1.10, 1.11, 3.13) and the production check of every part-2 epic.

## Outcome

The EU business runs on the platform; the signal is initiative Done when 1–3 met in production.

## Requirements

Completed at inception from the initiative's P14 and the source.

## Done when

1. Portal and admin site are live in EU production, the admin site on its own instance, both with backups, monitoring and alerts.
2. Part-1 held stories 1.7, 1.12, 1.10, 1.11 and 3.13 are done.
3. The production check of each of epics 1–14 passes (listed at inception). Each epic adds its checks below as a sub-list, by its "Production checks for the EU go-live" entry.
   - **epic-admin-access (entry 12)** — on admin.mytpdso.com, and for 3d over SSH on the admin instance. Maps to epic-admin-access Done when 1–4. List demo is gone by then (removed when epic-sales-finance's first list ships), so these checks use Admin users and Audit log; the Commercial Regional Manager's region scope is checked by the production check of the epic whose list replaces List demo.
     - Before (the real Sysadmin): in Admin users press **New** (a fresh code is asked) and create one test user per role (two Sysadmins: "Test Sysadmin" and "Test Sysadmin 2"), each with a reachable email; give the test Clinical User a mobile and the test Commercial Regional Manager one region only. Note each temporary password (shown once) and pass it on. After all checks: deactivate every test user (never delete them).
     - 3a (Done when 1, sign-in and roles; each test user): the first sign-in is password, then the 6-digit code by email, then the new-password page; once a new password is set, the user sees only their role's areas in the menu, and typing another area's URL (e.g. `/audit-log/` for a non-Sysadmin) gives a 403. The test Clinical User presses "Send it by SMS instead" on the code page and signs in with the SMS code. "Remember this device" skips the code at the next sign-in on that browser; signing in on a second browser ends the first session with "signed in on another device".
     - 3b (Done when 2, login page; signed out, then any test user): `/login/` shows only the MyTreatplan branding, the email and password form and "Contact your MyTreatplan administrator."; no signup, no "forgot password", no reset link. Signed out, every other page redirects to `/login/`; signed in, `/signup/` and `/password_reset/` give 404.
     - 3c (Done when 3, lockout and web reset; the test Clinical User, then the test Sysadmin): five wrong passwords on the test Clinical User lock it (the right password is refused for 10 minutes); Sentry shows the warning "staff account <uuid> locked"; every active Sysadmin (real and test) has an unread "Account locked: Test Clinical User" message in Inbox with the badge in the top bar, linking to that user; opening it drops the badge. The test Sysadmin then opens that user in Admin users and presses Reset password: a fresh code is asked even on a remembered device, the temporary password is shown once, the lock is lifted, and the user's next sign-in goes to the new-password page.
     - 3d (Done when 3, emergency reset over SSH; the operator with SSH access): in the admin instance's release folder (its site name is set when the production admin instance is provisioned, in this epic), run `sudo -u <site user> ./venv/bin/python manage.py reset_sysadmin_password --email <Test Sysadmin 2's email>`: it names the user and role, asks y/N, and on y prints the temporary password once; Test Sysadmin 2 signs in with it and a code and must choose a new password. The same command with the test Clinical User's email is refused ("is not a Sysadmin").
     - 3e (Done when 4, lists, confirmations and audit; the test Sysadmin): in Admin users and in Audit log, the fixed columns (ID, Created, Status, View more) are there; sort by a column, search (the search text does not appear in the URL), filter by a date range, open a row's View more; download the Audit log CSV and open it in Excel (accented characters, e.g. in a display name, show correctly). Edit a test user's display name, deactivate and reactivate one: each change asks for confirmation first and appears in Audit log with its actor, time (UTC) and before/after in View more; the 3d reset's entry has the actor `reset_sysadmin_password`.
4. A real first order goes from signup to approval in production.

## Boundaries

Releasing what epics 1–14 built. No new features.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P14)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Decision (user, 2026-10-29): epics 1–14 finish on staging; this epic runs their production checks.
- Waits on epics 1–14 because: it releases what they verified on staging.
- Decision (user, 2026-11-05, epic-admin-access entry 12): each epic's production checks are a sub-list under Done when 3; they run with temporary test users (one per role), deactivated afterwards.
