---
type: epic
title: "Clinical staff run cases from the admin site"
parent: initiative-mytreatplan-portal-admin
covers: [P10]
after: []
assignee: ""
risk: high
---

# Clinical staff run cases from the admin site

## Description

The Clinical area of the admin site: the clinical view of clients, patients per region, work orders, specialist calendars and availability, automatic pre-assignment with the 24 h confirmation, manual assignment, Clinical Users limited to their assignments, records download and "uploaded to maker" marking, maker-login reveal with every view logged, manual patients and manual work orders (shown to the doctor as pending payment), the team calendar and clinical reports.

## Outcome

Clinical staff run every case without spreadsheets; the signal is a paid case on staging pre-assigned, confirmed and taken to approval from the admin site.

## Requirements

Completed at inception from the initiative's P10 and the source.

## Done when

1. A paid case is pre-assigned to the specialist with the first free slot and the lightest load; it becomes firm after 24 h, or earlier when the Clinical Manager confirms or changes it; the doctor sees "assigned" only then.
2. A Clinical User sees only their assigned work orders and the related clients and patients, without financial data.
3. Records are downloaded (logged) and marked uploaded to the maker, which starts the 7-day deletion.
4. Revealing a doctor's maker login is logged with who and when.
5. A manual work order created for an existing patient appears on the doctor's dashboard as pending payment.

## Boundaries

Staff-side case work. Not appointments (epic-appointments), not sales data (epic-sales-finance).

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P10)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Handoff (2026-10-30, epic-ordering inception): this epic requests named stage transitions from epic-ordering's stage machine, creates manual patients and work orders through its services (entries 1 and 9), and uses its work-order events without patient data for workload figures (entry 8).
- Handoff (2026-10-30, epic-doctor-profile inception): maker logins are revealed through the service function epic-doctor-profile entry 8 exposes, called over remote reads (4.5), each view logged in the region.
- Handoff (2026-10-30, epic-admin-access inception): this epic applies the 'assigned only' scope for Clinical Users that epic-admin-access entry 6 defines, and uses its fresh-code check (entry 5) before patient-record downloads.
- Handoff: stage changes made here reach the portal through the remote API (epic-case-lifecycle sends the messages).
- Waits on epic 4 because: it needs remote API and client record.
- Waits on epic 6 because: it needs maker logins.
- Waits on epic 9 because: it needs work orders and pending-payment list.
- Waits on epic 10 because: it needs case stages and records.
