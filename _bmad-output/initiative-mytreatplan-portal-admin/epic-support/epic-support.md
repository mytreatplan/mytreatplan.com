---
type: epic
title: "Doctors and staff get help"
parent: initiative-mytreatplan-portal-admin
covers: [P13]
after: []
assignee: ""
risk: medium
---

# Doctors and staff get help

## Description

The support area on the doctor site (FAQ, knowledge base, tickets with attachments stored in the region; follow up and close) and in the admin site (handling and answering doctor tickets and admin-user tickets, kept apart).

## Outcome

Doctors solve or report problems without email; the signal is a ticket opened on staging with an attachment, answered from the admin site and closed by the doctor.

## Requirements

Completed at inception from the initiative's P13 and the source.

## Done when

1. A doctor reads the FAQ and the knowledge base (videos) in their language.
2. A doctor opens a ticket with a screenshot; the attachment stays in the region and is viewed remotely from the admin site.
3. Staff answer and change status; the doctor is notified and can close it.
4. Admin users open tickets of their own, listed apart from doctors' tickets.

## Boundaries

Support only. The knowledge base may start with little content.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P13)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Waits on epic 3 because: it needs admin users and roles.
- Waits on epic 4 because: it needs remote API for regional attachments.
