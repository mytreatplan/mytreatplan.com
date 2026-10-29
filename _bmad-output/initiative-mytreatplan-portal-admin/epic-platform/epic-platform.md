---
type: epic
title: "Two sites, two regions: platform baseline"
parent: initiative-mytreatplan-portal-admin
covers: [P14, P1]
after: []
assignee: ""
risk: high
---

# Two sites, two regions: platform baseline

## Description

The ground every other epic builds on: the admin site as its own repo, deployed to admindev.mytpdso.com with CI; the portal driven by a REGION profile, with a second staging site running REGION=ae; global UUIDs on the records the admin site will consolidate; a background job runner for both sites; regional object storage. It delivers the P1 parts that are platform (REGION profile, global ids) and all of P14 except production, which epic-eu-golive runs.

## Outcome

The team can build either site for either region on staging; the signal is both staging sites (eu and ae) and admindev deploying from master with green CI.

## Requirements

Source: the initiative's P14 and P1 and the architecture spine (architecture-portal-admin), AD-5, AD-8, AD-9, AD-10, AD-12, AD-13, AD-23, AD-24.

- E1 (P14): Site-aware deploy tooling: provision.sh, release.sh, systemd units, jobs, nginx and CI take a site name; staging runs dev, dev-ae and admindev (AD-12).
- E2 (P1): REGION profile module: domains, region company, default time zone, languages, currencies, storage endpoint, KMS, gateways, patient-code and invoice prefixes; no region-name comparisons outside it (AD-13).
- E3 (P1): Global ids: an immutable uid UUID on part-1 tables that cross sites; new crossing tables use a UUID primary key (AD-5).
- E4 (P14): Background work: django.tasks with a PostgreSQL-backed worker per site; on_commit enqueue; idempotency convention; monitors named by site (AD-8).
- E5 (P14): Regional object storage: private encrypted bucket per region, presigned multipart browser upload, quarantine and clean prefixes, presigned GET of at most 5 minutes with a logging hook, delete keeping only the trace, CORS exposing ETag, lifecycle abort after 7 days (AD-9).
- E6 (P14): Virus scanning: ClamAV 1.4.6 or later; files the scanner cannot fully scan are rejected (AD-9).
- E7 (P14): mytp-core repo: tests, CI, semver tags, wheel vendored into release tarballs; field crypto, KMS and shared operations code (Sentry and scrubber, runjob, healthz, backupdb) moved in (AD-10, AD-23).
- E8 (P14): Admin repo skeleton deployed to admindev with CI, noindex and disallow-all robots, AdminLTE static layout, mytp-core installed (AD-11, AD-12).
- E9 (P14): Operations per site on staging: backups per site database, Sentry environment and slugs per site, restore test on a server (AD-24).

## Done when

1. admindev.mytpdso.com serves the admin site from its own repo, deployed by CI on every push to master, behind the team password and hidden from robots.
2. A second portal staging site runs the same build with REGION=ae, and the region-specific values (domain, company, time zone, languages, currencies) come from the profile, not from code branches.
3. Every record the admin site will consolidate has an immutable UUID; existing part-1 rows are migrated.
4. A scheduled job and a queued task run on both sites on staging, with failures alerted as in part 1.
5. A large file uploads to regional object storage from staging and is deleted by a job.
6. Production checks for these are listed in epic-eu-golive.

## Boundaries

The platform both sites stand on. Not the country map or routing (epic-regions), not admin users (epic-admin-access). The architecture document settles the shared decisions before this epic starts.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P14, P1)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Tracer bullet: entry 1. dev redeploys under the per-site layout, proving the tooling every other site uses.
- Least certain: entry 2 (the OVH browser upload), run at once in parallel with entries 1 and 3.
- Lanes: deploy tooling (1 → 7, then 10, 13); shared code (3 → 4 → 5 → 6 → 9); storage (2 → 11 → 12).
- Decision (user, 2026-10-29): dev-ae on staging uses the EU KMS and EU object storage, with its own database, bucket and backup prefix; the UAE setup comes with epic-uae-launch.
- Decision (user, 2026-10-29): repos mytreatplan/mytp-core and mytreatplan/mytp-admin, private; servers receive mytp-core vendored in the release tarball; only CI holds a secret to fetch it.
- Decision (user, 2026-10-29): the django.tasks backend is django-tasks-db, confirmed on Django 6.1 by entry 9.
- Decision (2026-10-29): release.sh is installed host-wide by provision.sh and deploys any repo's tarball to its site; the deploy tooling stays in the portal repo.
- Decision (2026-10-29): the Money type is built by epic-catalogue, the patient-code generator by epic-ordering, and site signing-key storage by epic-admin-interface on this epic's key provider.
- Decision (user, 2026-10-29): 15 entries kept in one epic (one owner, mostly one lane).
- Waits on the architecture document (bmad-architecture) for the shared decisions: REGION profile, data placement, ids and patient code, interface contract, money and VAT, job runner, object storage, code shared with the admin site, admin UI template.
