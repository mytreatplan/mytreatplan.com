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

Admin users separate from doctors, with the six roles and their permissions; the 2FA login reused from part 1 (6-digit code, remember this device for 90 days, email/SMS/WhatsApp); no signup and no web password recovery, with a superuser reset in the panel and an SSH-run command for emergencies; a dashboard frame per role that later epics fill with their own widgets; the list conventions every later list follows; the audit log; admin user management; my profile and my messages and alerts.

## Outcome

Staff reach only what their role allows; the signal is each role signing in with 2FA on admindev and seeing only its areas.

## Requirements

Completed at inception from the initiative's P2 and the source.

## Done when

1. A user of each of the six roles signs in with 2FA on admindev and sees only its areas; a Clinical User sees only what is assigned to them.
2. The login page shows only the branding and the "contact support" message; there is no signup and no password-reset link.
3. A Sysadmin resets another admin's password from the panel, and the SSH command resets the superuser's.
4. A sample list shows CSV export, column ordering, search, the fixed fields and view more; every change asks for confirmation and lands in the audit log.

## Boundaries

Who can enter the admin site and what they may see. Not the content of the areas (later epics), not the system logs area (Parked).

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P2)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Handoff: later epics add their widgets to the role dashboards and their lists follow the conventions built here.
- The admin UI template and how the part-1 login code is shared come from the architecture document.
- Waits on epic 1 because: it needs admin repo, admindev staging and CI.
