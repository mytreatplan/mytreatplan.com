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
3. The production check of each of epics 1–14 passes (listed at inception).
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
