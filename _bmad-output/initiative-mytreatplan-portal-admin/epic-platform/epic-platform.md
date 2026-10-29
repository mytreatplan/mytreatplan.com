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

Completed at inception from the initiative's P14, P1 and the source.

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

- Waits on the architecture document (bmad-architecture) for the shared decisions: REGION profile, data placement, ids and patient code, interface contract, money and VAT, job runner, object storage, code shared with the admin site, admin UI template.
