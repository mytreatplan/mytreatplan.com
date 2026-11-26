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

From the initiative's P9, the requirements document (doctor site: patient list, patient file, case stages) and AD-9, AD-15, AD-19, AD-21, AD-23.

- L1 Patient list: code, name or reference, registering date and current stage; inactive patients stand out.
- L2 Patient file: the doctor's comments, the current service and its stage, the history of services and of stage changes, and the appointments block epic-appointments fills.
- L3 Case stages: Pending assignment → Assigned → (Pending registers upload) → (Processing prescription) → (Prescription done) → Pending appointment → In development → Pending review → Pending approval → Approved; staff request named moves over the remote API (AD-21), every move goes through one portal service (AD-15).
- L4 Records upload for VirtuaOrtho and in-house services: one compressed file per work order up to 1 GiB, resumable after a dropped connection, virus-scanned, with its status (uploaded and waiting, processed, queued for deletion with the date, deleted with the date).
- L5 Records deletion: 7 days after staff mark them uploaded to the maker, leaving only the trace.
- L6 Patient deletion: marked by the doctor or staff, deleted 48 h later, logically (archived, then removed from the live tables), leaving the trace (internal patient id, who marked, when).
- L7 Case approval: a VirtuaOrtho or in-house case in Pending approval is approved by the doctor after a confirmation.
- L8 STL download: an approved in-house case offers its STL files to the doctor.
- L9 Messages: the doctor gets an email on assignment and on Pending appointment, Pending review and Pending approval, with SMS when the email cannot be sent; patients by code only.
- L10 Assignment: the region holds the assignee and the 24 h confirmation; the doctor sees Assigned only once it is firm.
- L11 Inactive: the doctor sets a patient active or inactive.

## Done when

1. The patient list and file show code, stage, history and comments; inactive patients stand out.
2. A 1 GB record set uploads, survives an interrupted connection, is virus-scanned and shows its status through processing and deletion.
3. A patient marked for deletion is archived and removed from the live tables after 48 h, and records are deleted 7 days after being marked uploaded to the maker, leaving only the required trace.
4. A doctor approves a case in "pending approval" after confirming; an in-house case then offers its STL download.
5. The doctor gets an email (SMS as fallback) on assignment and on pending appointment, pending review and pending approval.

## Boundaries

The case on the portal. Staff actions are epic-clinical-ops's; appointments are epic-appointments's.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P9)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — architecture-portal-admin/architecture-portal-admin.md (AD-2, AD-8, AD-9, AD-15, AD-19, AD-21, AD-23)

## Notes

- Handoff (2026-10-30, epic-ordering inception): the case stage machine (stages, transitions, one live work order per patient) is built by epic-ordering entry 1 in mytp-core; this epic reacts to it and extends the patient list built in epic-ordering entry 2 with stage, history and comments.
- Handoff: epic-clinical-ops moves stages through the remote API; this epic reacts and sends the messages.
- Handoff: epic-appointments fills the appointments block this epic leaves on the patient file.
- Waits on epic 1 because: it needs object storage and job runner.
- Waits on epic 9 because: it needs work orders and patients.
- Decision (user, 2026-11-26, inception): SMS only as a fallback, when the email cannot be sent (address refused or send failed).
- Decision (user, 2026-11-26): STL files: this epic builds the STL storage purpose, the doctor's download and a remote "attach STL" route; epic-clinical-ops builds the staff upload screen.
- Decision (user, 2026-11-26): assignment lives in the region (assignee, 24 h confirmation job, the "assigned" message); epic-clinical-ops proposes and confirms the assignee on the admin site.
- Decision (user, 2026-11-26): records are a single compressed file per work order (recommended to the doctor), up to 1 GiB; file types are not limited, since they vary. The doctor ends with "Records complete", which moves the case to Processing prescription. Note: the virus scan rejects what it cannot fully scan, so a password-protected archive is refused; the page says so.
- Decision (user, 2026-11-26): patient deletion is refused while the patient has a live work order. After 48 h the patient, their work orders, portrait and records go; orders and invoices keep the patient code only; the trace keeps no file name (AD-9 over the requirements document's file name). It must not conflict with long-term storage (user): the deletion is logical as decided for 9.12 (2026-11-22) — the patient and work orders are archived to the regional write-once archive first, then removed from the live tables.
- Decision (user, 2026-11-26): inactive is set by the doctor on the patient file and keeps today's meaning (no new orders; highlighted in the list; still listed).
- Decision (user, 2026-11-26): case messages go to the login email, as the order emails (9.20).
- Decision (user, 2026-11-26, from 9.11's scope): the findings deferred by epic-ordering's unfinished entries (9.10 production checks, 9.13 archive report) go to this epic's refactor sweep.
- Decision (user, 2026-11-26): staff may also mark a single patient for deletion (the requirements document: "Same if this patient is marked for deletion from Admin"); AD-19's 48 h rule applies to both, and the deletion is logical (archive, then remove).
- Decision (user, 2026-11-26, breakdown approved): tracer bullet is entry 1 (named stage moves through the remote API, shown on the patient file); entry 3 (SMS, the least certain: the Twilio sender) comes right after the email service. Lanes after 1: messages 2 → 3 → 10; patient pages and files 4 → 5 → 6 → 8 → 9 → 7. Assignment (10) comes late because it builds on 1's Assign move and on both message channels.
- Assumption (inception, 2026-11-26; the user may correct): a record's "processed" status means the case reached Prescription done; Approved ends the live work order (mytp-core's LIVE_STAGES already excludes it); patient deletion is refused with a live or pending-payment work order and removes the patient's cart items; pre-assigned is a field on the work order, not a stage.
- Handoff (2026-11-26, to epic-clinical-ops): the portal's remote routes this epic adds and the admin site calls: named stage moves (10.1), records download and "uploaded to the maker" mark (10.6), attach STL (10.9), patient deletion mark (10.7), assignee proposal and confirmation (10.10).
- Handoff (2026-11-26, to epic-appointments): the patient file's appointments block is a named template slot added by 10.4.

