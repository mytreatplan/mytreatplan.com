---
type: epic
title: "Staff sign in to the admin site by role"
parent: initiative-mytreatplan-portal-admin
covers: [P2]
after: []
assignee: ""
risk: high
---

# Staff sign in to the admin site by role

## Description

Admin users separate from doctors, with the six roles and their permissions; the 2FA login reused from part 1 through mytp-core (6-digit code by email or SMS, WhatsApp when the portal's sender is approved; remember this device for 7 days; a fresh code before sensitive actions); no signup and no web password recovery, with a superuser reset in the panel and an SSH-run command for emergencies; a dashboard frame per role that later epics fill with their own widgets; the list conventions every later list follows; the audit log; admin user management; my profile and my messages and alerts.

## Outcome

Staff reach only what their role allows; the signal is each role signing in with 2FA on admindev and seeing only its areas.

## Requirements

Source: the initiative's P2 and the architecture spine, AD-4, AD-5, AD-10, AD-11, AD-17, AD-22, AD-23.

- A1 (P2): Admin users in their own model (UUID, never hard-deleted) with exactly one of six roles; a Commercial Regional Manager is limited to one or more regions from the admin's region registry.
- A2 (P2): 2FA sign-in through mytp-core: separate Twilio Verify service, email or SMS (WhatsApp later), remember this device 7 days, the portal's lockout rule with a Sysadmin alert, single session, a fresh code before sensitive actions; branded login page with no signup and no web reset.
- A3 (P2): One permission matrix (role × area × action) used by every view and menu, with region limits (AD-17).
- A4 (P2): A dashboard frame per role with widget slots later epics fill.
- A5 (P2): List conventions: sorting, POST search, CSV of central data, fixed columns, confirmation before every change (AD-11).
- A6 (P2): Audit log of every admin-site change.
- A7 (P2): Admin user management; Sysadmins reset passwords to a temporary one, shown once, changed at next sign-in.
- A8 (P2): Emergency Sysadmin reset by a command over SSH.
- A9 (P2): My profile and an in-app messages and alerts inbox.

## Done when

1. A user of each of the six roles signs in with 2FA on admindev and sees only its areas; the 'assigned only' scope for Clinical Users is defined here and applied by epic-clinical-ops.
2. The login page shows only the branding and the "contact support" message; there is no signup and no password-reset link.
3. A Sysadmin resets another admin's password from the panel, and the SSH command resets the superuser's.
4. A sample list shows CSV export, column ordering, search, the fixed fields and view more; every change asks for confirmation and lands in the audit log.
5. Production checks for these are listed in epic-eu-golive (entry 12).

## Boundaries

Who can enter the admin site and what they may see. Not the content of the areas (later epics), not the system logs area (Parked).

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P2)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — architecture-portal-admin/architecture-portal-admin.md, AD-4, AD-5, AD-10, AD-11, AD-17, AD-22, AD-23

## Notes

- Tracer bullet: entry 1 (an admin user signs in on admindev to a role dashboard frame).
- Lanes: admin site (1 → 6 → 7 → 8); shared 2FA (2 → 3 → 4 → 5); they join at 9.
- Decision (user, 2026-10-30): one role per admin user; a Commercial Regional Manager may cover one or more regions.
- Decision (user, 2026-10-30): staff codes come from a separate Twilio Verify service; email and SMS now, WhatsApp together with the portal's once Meta approves the sender.
- Decision (user, 2026-10-30): a Sysadmin resets a password to a temporary one passed on out of band; the user changes it at the next sign-in.
- Decision (user, 2026-10-30): alerts are in-app only (inbox and badge).
- Decision (user, 2026-10-30): the portal's lockout rule (5 failures in 24 h lock for 10 minutes) plus an inbox alert to Sysadmins.
- Handoff: later epics add their widgets to the role dashboards and their lists follow the conventions built here.
- The admin UI template and how the part-1 login code is shared come from the architecture document.
- Waits on epic 1 because: it needs admin repo, admindev staging and CI.
