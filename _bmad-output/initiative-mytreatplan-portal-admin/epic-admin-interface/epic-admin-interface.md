---
type: epic
title: "Admin and regions exchange data within the rules"
parent: initiative-mytreatplan-portal-admin
covers: [P3]
after: []
assignee: ""
risk: high
---

# Admin and regions exchange data within the rules

## Description

The interface between the admin site and each regional instance: the configuration channel down, business events and the accounting feed up, and the remote-access API for patient and clinical data that the admin site never stores; service authentication, encryption and signing; access logging in the region. It also owns the client record: doctor data synced to the admin site. Contact messages from the public site arrive here as an event, replacing part-1 story 1.6.

## Outcome

Every later epic exchanges data through one contract that keeps patient data in its region; the signal is a config change, an event and a remote read round-tripping between admindev and both staging regions, each logged.

## Requirements

Completed at inception from the initiative's P3 and the source.

## Done when

1. A configuration change made in the admin site reaches both staging regions; an instance keeps working while the admin site is down and catches up after.
2. A doctor signing up on either staging region appears as a client in the admin site; a contact message appears as an event.
3. An admin user reads and updates a sample patient record through the remote API; the record is not stored in the admin database, and the region logs who accessed it, from where and when.
4. A request with a bad signature or from an unknown service is refused and logged.

## Boundaries

The channels, the contract and the client record. Each later epic adds its own events and endpoints on this contract; this epic does not build them.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P3)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Handoff (2026-10-30, epic-admin-access inception): remote-access tokens carry the role and permitted regions in the form epic-admin-access entry 6 exposes; the admin's region registry (entry 1) lists the instances.
- Decision (2026-10-29, epic-platform inception): this epic stores each site's Ed25519 signing keys, wrapped by the site's KMS through epic-platform's key provider (AD-4).
- Handoff: epic-sales-finance shows the clients list and contact messages; epic-clinical-ops shows the clinical view; both extend this epic's client record.
- Dropped from part 1: 1.6 (CRM forwarding), replaced by the contact-message event (user, 2026-10-25).
- Waits on epic 1 because: it needs admin repo, job runner, REGION profile.
- Waits on epic 3 because: it needs admin users and roles for remote access.
