---
title: 'Production checks for the EU go-live (epic-catalogue)'
type: 'chore'
ticket: '14'
created: '2026-11-13'
status: 'done'
baseline_revision: '442f745ff5c3868024613c7eeb2a0941d7b39f5a'
route: 'oneshot'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** epic-eu-golive's Done when 3 lists the production checks of epic-admin-access and epic-platform only. Nothing says how to prove in EU production that the catalogue works: sync to the portal, prices per country group, billing rules per country, vouchers per region, the country map and the rate job.

**Approach:** Add a sub-list "epic-catalogue (entry 14)" under Done when 3 of `epic-eu-golive/epic-eu-golive.md`, in the form of the epic-platform one (header, Before, lettered checks from 3n, clean-up), with one runnable check per Done when item of epic-catalogue (1–4) plus the sync and the rate job. Add one Notes line to each of the two epics. Docs only.

**Decisions (user, 2026-11-14):**
- The checks of Done when 1 run on the real catalogue: the first real product staff type, its real USD clone and its real subscription links. No test rows are created. The AED clone belongs to region ae: its check is named as epic-uae-launch's.
- The country-map check sets Antarctica (AQ) to banned, reads it on portal-eu, then sets it back to eu.
- The voucher check uses one general try-me voucher for eu: created, read on portal-eu, deactivated, read again. A voucher for one doctor is left to epic-subscriptions' production check.
- The story lands as one docs commit on master with this plan inside, pushed directly, no PR; nothing to deploy or check on staging.
- The checks run after staff typed the real companies, the rules with the tax advisor's texts and one stored ECB rate; `seed_staging_catalogue` refused on the admin site is a negative check; the AE company and AED prices are epic-uae-launch's.

</frozen-after-approval>

## Code Map

- `_bmad-output/initiative-mytreatplan-portal-admin/epic-eu-golive/epic-eu-golive.md` -- Done when 3: add the sub-list after 3m; Notes: one Decision line. Change nothing else.
- `_bmad-output/initiative-mytreatplan-portal-admin/epic-catalogue/epic-catalogue.md` -- Notes: one line pointing at the sub-list.
- `epic-platform/story-production-checks-for-the-eu-go-live-plan.md` -- the model for form and verification (read the result; no check is run in production).
- Portal commands (read-only unless said): `integration/management/commands/pull_config.py` (`pull_config: v<n> applied|unchanged|refused (<code>)`), `show_config.py`, `catalogue/management/commands/price_catalogue.py` (`--country --tier --category --subscription --product --voucher --order --vies --buy`), `regions.countries.lookup_country`.
- Admin commands: `interface/management/commands/nudge_config.py` (`<site>: ok`), `catalogue/management/commands/fetch_rates.py` (job `fetchrates`, 16:30 UTC, monitor `mytp-admin-fetchrates`), `seed_staging_catalogue` (refused on a public deploy by `interface/checks.py` `public_deploy()`: usable as a negative check). Pages: `/catalogue/`, `/catalogue/rates/`, `/billing/rules/`, `/vouchers/`, `/country-map/<code>/edit/`.
- Roles: catalogue = Commercial Manager + Sysadmin; billing = Financial Manager + Sysadmin; country map = Sysadmin.
- Facts for the Before block: staff type the real catalogue first (products, subscriptions, ES and IE companies with real identifiers, rules with the tax advisor's texts; the IE company must match the eu profile, MyTPDSO Limited, 815526); one stored ECB rate before any clone; `add_peer_key eu ... --site portal-eu` on admin; no SEED-*/STG* row, "STAGING" text or made-up identifier in production; AE on portal-eu answers "not served by this region".

## Tasks & Acceptance

**Execution:**
- [x] `_bmad-output/initiative-mytreatplan-portal-admin/epic-eu-golive/epic-eu-golive.md` -- under Done when 3, after 3m, add the sub-list "epic-catalogue (entry 14)": header (sites `portal-eu` and `admin`, maps to epic-catalogue Done when 1–4, ae left to epic-uae-launch), Before (prerequisites, roles, no staging data), then 3n sync, 3o product / USD clone / link / offered to the matching country only (Done when 1), 3p one standard subscription per tier and group (2), 3q billing rules for ES, PT, IE, DE, US and AE not served, with the VIES reverse charge (3), 3r voucher (4), 3s country map (4), 3t rate job, and the clean-up -- one runnable check per item.
- [x] same file, Notes -- one Decision line (user, 2026-11-14) with the decisions above.
- [x] `_bmad-output/initiative-mytreatplan-portal-admin/epic-catalogue/epic-catalogue.md` -- Notes: one line saying the production checks are in epic-eu-golive Done when 3 (3n–3t).

**Acceptance Criteria:**
- Given epic-catalogue's Done when 1–4, when the sub-list is read, then each item is named by at least one lettered check with its command or page, its expected output and its clean-up.
- Given a command or flag named in a check, when it is looked up in the portal or mytp-admin at master, then it exists with that name.
- Given the diff, when it is read, then only the two epic files and this plan changed, and nothing else in epic-eu-golive moved.

## Implementation Notes

Oneshot: one sub-list and two Notes lines in planning documents, under 100 lines, no code.

- `epic-eu-golive/epic-eu-golive.md`: the sub-list "epic-catalogue (entry 14)" after 3m (header, Before, 3n sync, 3o Done when 1, 3p Done when 2, 3q Done when 3, 3r and 3s Done when 4, 3t rate job, clean-up) and one Decision line in Notes. `epic-catalogue/epic-catalogue.md`: one Notes line.
- Every command, flag, URL, role and quoted message was read in the code at portal 442f745 and mytp-admin e1e435a. No check was run in production (that is epic-eu-golive's work).
- Choices: 3o only reads what the real Commercial Manager made (the clone, its link, its activation are prerequisites in Before); Audit log steps are done by the test Sysadmin (Audit log is Sysadmin only); 3s also tries to ban ES, which is refused while Iberia lists it; `seed_staging_catalogue --dry-run` refused on the admin instance is in Before as the negative check.
- Landed as one docs commit on master, no PR, nothing deployed, no staging check (user decision).

## Plan Change Log

## Review Triage Log

Quick lens, one pass (2026-11-14): 8 findings, 0 high, 3 medium, 5 low, 0 false; all patched in the sub-list.

| # | Finding | Verdict | Action |
|---|---------|---------|--------|
| 1 | 3o, 3p, 3r had the Commercial Manager read Audit log, which is Sysadmin only (`audit/views.py`) | medium | patched: the test Sysadmin reads it |
| 2 | 3t expected `Stored ...` on the first manual start, but the timer has usually stored that date already | medium | patched: either line accepted, the second start always `Nothing stored` |
| 3 | Before said "3t stores" the rate needed before the first clone, though 3t runs later | low | patched: the rate comes from the timer or a manual start, in Before |
| 4 | 3q's reverse-charge check needs a reverse-charge text on the rule (`catalogue/pricing.py:332-334`), not stated | medium | patched: stated in Before |
| 5 | 3p's portal follow-up used ES whatever the group chosen | low | patched: a country of that group |
| 6 | Before dropped the Irish company number 815526, which the profile check compares | low | patched |
| 7 | 3o was run by the real Commercial Manager, against the test-user Decision of 2026-11-05 | low | patched: 3o is read only, by the test Sysadmin; the new Decision line says so |
| 8 | 3o read as if the check made the clone | low | patched: the clone is a prerequisite in Before |
