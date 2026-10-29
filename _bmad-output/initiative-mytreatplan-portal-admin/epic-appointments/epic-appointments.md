---
type: epic
title: "Doctors and specialists meet on schedule"
parent: initiative-mytreatplan-portal-admin
covers: [P11]
after: []
assignee: ""
risk: medium
---

# Doctors and specialists meet on schedule

## Description

Appointments on both sides: the doctor requests one from the case page at least 72 h ahead within the stage rules, the specialist confirms; the specialist records appointments arranged by WhatsApp or email; the meeting link (entered manually for now) on the case page; call counts and length limits per product and subscription with a warning to the doctor; my calendar for admin users.

## Outcome

Calls happen on agreed slots within each product's allowance; the signal is a doctor on staging booking, the specialist confirming, and the link and remaining calls shown on the case page.

## Requirements

Completed at inception from the initiative's P11 and the source.

## Done when

1. A doctor sees only slots at least 72 h ahead from the assigned specialist and only in stages that allow it; the specialist confirms and the doctor is notified.
2. A specialist records an appointment arranged outside the site, and it appears on the case page.
3. The case page shows the meeting link and the calls left; the doctor is warned at the last call; Premium and Spark Advanced extras are counted.
4. Each admin user sees their own calendar.

## Boundaries

Appointments only. Not the Zoom API or the account pool (Parked).

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P11)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Waits on epic 10 because: it needs case page appointments block.
- Waits on epic 11 because: it needs specialist calendars, availability and assignment.
