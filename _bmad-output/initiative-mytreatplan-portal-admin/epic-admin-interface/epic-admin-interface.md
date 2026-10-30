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

Source: the initiative's P3 and the architecture spine, AD-1–AD-4, AD-12, AD-15, AD-17–AD-19, AD-22, AD-23.

- I1 (P3): Signed calls in both directions with the AD-4 token rules; every interface call, accepted or refused, logged on the receiving site.
- I2 (P3): Per-site Ed25519 keys wrapped by the site's KMS; several keys per peer by kid for rotation; revocation runbook; nginx peer allowlist on /api/ in production.
- I3 (P3): Contract definitions in mytp-core: envelope, error and amount shapes, /api/v<n> versioning with n and n−1 served, readers ignore unknown fields; contract tests (AD-12, AD-23).
- I4 (P3): Outbox and ingest: same-transaction writes, per-aggregate order, retries on the row, dedupe and version checks, region-signer check, failed events visible, 30-day purge (AD-3, AD-19).
- I5 (P3): Configuration snapshot: signed, versioned, pulled every 5 minutes and on nudge, applied atomically, last one kept (AD-3, AD-22).
- I6 (P3): Remote reads: instance-side permission checks and field omission, regional access log written before returning data (fail closed), no-store, nothing persisted centrally (AD-4, AD-17, AD-23).
- I7 (P3): Remote writes with idempotency key and entity version, 409 when stale, through the service layer (AD-3, AD-15).
- I8 (P3): Client record sync with versions, tombstones and re-sync; erasure deletes the central copy (AD-18).
- I9 (P3): Contact messages copied centrally like client records; the email to the company inbox stays.
- I10 (P3): Hub lookups: live region-to-admin reads, limited by the spine (AD-3).

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
- architecture — architecture-portal-admin/architecture-portal-admin.md, AD-1–AD-4, AD-12, AD-15, AD-17–AD-19, AD-22, AD-23

## Notes

- Tracer bullet: entry 1 (a signed ping both ways between admindev and dev).
- Lanes: one lane on the portal's integration app and the admin's endpoints (1 → 2 → 3 → 4 → 5 → 6, with 7 after 5); sync (8 → 9) after 3; operations (10) after 3 and 4.
- Decision (2026-10-30): the contract format is pydantic 2 models exported as JSON Schema, pinned in mytp-core; this closes the spine's Deferred item.
- Decision (2026-10-30): remote reads are proved on a staging-only sample record behind a setting, removed when epic-ordering's patients exist.
- Decision (user, 2026-10-30): the contact form keeps emailing the company inbox in addition to the admin site, until epic-sales-finance's message list exists.
- Waits on epic 2 because: client records carry the home region (2.2).
- Handoff (2026-10-30, epic-admin-access inception): remote-access tokens carry the role and permitted regions in the form epic-admin-access entry 6 exposes; the admin's region registry (entry 1) lists the instances.
- Decision (2026-10-29, epic-platform inception): this epic stores each site's Ed25519 signing keys, wrapped by the site's KMS through epic-platform's key provider (AD-4).
- Handoff: epic-sales-finance shows the clients list and contact messages; epic-clinical-ops shows the clinical view; both extend this epic's client record.
- Dropped from part 1: 1.6 (CRM forwarding), replaced by the contact-message event (user, 2026-10-25).
- Waits on epic 1 because: it needs admin repo, job runner, REGION profile.
- Waits on epic 3 because: it needs admin users and roles for remote access.
