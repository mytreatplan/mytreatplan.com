---
type: epic
title: "Doctors follow each case to approval"
parent: initiative-mytreatplan-portal-admin
covers: [P9]
after: []
assignee: ""
risk: high
---

# Doctors follow each case to approval

## Description

The doctor's side of a case: the patient list and patient file, the case stages, records upload to regional object storage (large, resumable, virus-scanned) with status, the deletion jobs, case approval, STL download for in-house printing, and the automatic messages by email with SMS as fallback. Stage changes arrive from the admin site through the remote API (epic-clinical-ops); the patient file keeps an appointments block that epic-appointments fills in.

## Outcome

Doctors know where every case stands and do their part on the site; the signal is a case on staging moving through every stage with the doctor notified at each one that needs them.

## Requirements

Completed at inception from the initiative's P9 and the source.

## Done when

1. The patient list and file show code, stage, history and comments; inactive patients stand out.
2. A 1 GB record set uploads, survives an interrupted connection, is virus-scanned and shows its status through processing and deletion.
3. A patient marked for deletion is physically deleted after 48 h and records 7 days after being marked uploaded, leaving only the required trace.
4. A doctor approves a case in "pending approval" after confirming; an in-house case then offers its STL download.
5. The doctor gets an email (SMS as fallback) on assignment and on pending appointment, pending review and pending approval.

## Boundaries

The case on the portal. Staff actions are epic-clinical-ops's; appointments are epic-appointments's.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P9)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Handoff: epic-clinical-ops moves stages through the remote API; this epic reacts and sends the messages.
- Handoff: epic-appointments fills the appointments block this epic leaves on the patient file.
- Waits on epic 1 because: it needs object storage and job runner.
- Waits on epic 9 because: it needs work orders and patients.
