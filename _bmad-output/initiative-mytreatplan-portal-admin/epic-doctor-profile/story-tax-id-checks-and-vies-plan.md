---
title: 'Tax id checks and VIES'
type: 'feature'
ticket: '6'
created: '2026-11-17'
status: 'done'
baseline_revision: 'f9e4b2dba5f4a03ea9926fa4df4ea70e58b88f16'
route: 'full'
route_source: 'pinned'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Fiscal entities (6.5) accept any tax id, and nothing tells the pricing resolver (5.12) whether an EU VAT number is valid, so reverse charge can never apply (D5).

**Approach:** Add per-country tax-id checks inside `fiscal.problems` and a VIES check for EU billing countries. The result is a service-owned `vies_status` on `FiscalEntity` that uses the resolver's own values (`valid`, `invalid`, `pending`, `not_applicable`). When VIES is unavailable, the entity is saved as `pending` and a scheduled job retries until VIES answers. The resolver already charges VAT for anything but `valid`. A helper hands the status to `price_order_for`.

**Decisions (user, 2026-11-17):**
- **VIES interface:** the EU REST API (`POST …/taxation_customs/vies/rest-api/check-vat-number`, JSON over urllib). No new HTTP dependency.
- **Checksums:** add `python-stdnum`, pinned in `requirements.txt`.
- **Spain:** a person must give a DNI-NIF or an NIE, and a company a CIF. A wrong checksum or a wrong kind of id refuses the save with a field error.
- **Portugal:** the NIF/NIPC mod-11 checksum. A wrong checksum refuses the save.
- **Other EU countries:** the tax id stays free text and is never refused. When it is not a well-formed VAT number for its country (stdnum `eu.vat`), the status is `invalid` and VIES is not called.
- **UAE:** the VAT id, when given, must be exactly 15 digits. The AE tax id stays free text.
- **Which entities get a VIES check:** every entity whose billing country is one of the EU-27, person or company. Greece is sent as `EL`. Every other country (GB, CH, NO, AE …) is `not_applicable` and VIES is not called. A VIES `invalid` never blocks the save; VAT is simply charged.
- **Country prefix:** the id may be typed with or without its country prefix (`EL` for Greece). It is stored normalised as typed (the 6.5 behaviour). The checks run on the national part. VIES gets the billing country's code plus the national part. A prefix that names another country is refused.
- **When VIES is called:** during the save request, after the offline check and before the locked write, with `EXTERNAL_CHECK_TIMEOUT`. Unavailability saves the entity as `pending`. Unavailability means:
  - a timeout or network error;
  - `MS_UNAVAILABLE`, `SERVICE_UNAVAILABLE`, `MS_MAX_CONCURRENT_REQ`, `GLOBAL_MAX_CONCURRENT_REQ` or `TIMEOUT`;
  - an HTTP 5xx or 429 answer;
  - an answer that cannot be parsed.
- **Retry job:** `retry_vies` runs every 15 minutes. Each entity's next try is 15 min, then 1 h, then every 6 h, until VIES answers. After 3 days still pending, the job logs one error for the monitor, carrying the entity id only.
- **Re-checks:** an edit that changes the tax id or the billing country resets the status and checks again. Other edits keep the status. There is no periodic re-check; re-checking at checkout goes to deferred-work for epic-payments.
- **What the list card shows:**
  - `valid`: "VAT number verified (VIES)"
  - `invalid`: "Not found in VIES – VAT will be charged"
  - `pending`: "VIES check pending"
  - `not_applicable`: nothing
  - There is no "check again" button.
- **Existing rows:** the migration sets EU rows to `pending`, due now, and the other rows to `not_applicable`. An existing id that fails the new checks stays usable. Its card shows "Please check the tax id", and the next edit must fix it, because the full form is checked again.
- **Merge and staging are delegated.** Every check runs on dev in a rolled-back transaction. I first confirm, read-only, that dev can reach the VIES service. The checks:
  - an invalid NIF is refused;
  - a real public DE VAT number becomes `valid`;
  - the job turns a forced `pending` into `valid`;
  - `price_order_for` receives the status.
- **Models:** a Sonnet implementer and an Opus reviewer with a security focus.

## Boundaries & Constraints

**Always:**
- Status values are `catalogue.pricing.VIES_*`, never a new vocabulary.
- The status, check time and retry fields are written only by the service. They are never in `FIELDS`, and a posted value is ignored.
- No network call while a row lock is held. The job and the save call VIES outside the transaction. They write the result under the lock only if the entity's tax id index and country still match what was checked.
- HTTP goes through `accounts/http.py` with `FETCH_ERRORS` and the settings timeout. Tests use an autouse fake, as `FakeExternalChecks` does, and `no_network` stays.
- Retries live on the entity row (the settings.py:427 convention). The job is a management command plus a `deploy/jobs.conf` line.
- Archived entities are never re-checked.
- Every new string is translated (formal "usted").

**Never:**
- No tax id or VAT number in logs, page records, the event log or exception messages.
- No `client.updated` change or core release (entry 9). No admin-site change. No change to the resolver's logic or its callers' defaults.
- A VIES answer never refuses a save. Only the offline checks for ES, PT and the AE VAT id do.

## I/O & Edge-Case Matrix

| # | Situation | Expected |
|---|---|---|
| 1 | ES person, DNI with a wrong letter | Field error on the tax id; nothing saved; VIES not called |
| 2 | ES company with a valid DNI (not a CIF), or an ES person with a CIF | Field error (wrong kind of id) |
| 3 | ES company, valid CIF, typed with or without "ES" | Saved; VIES gets `ES` + the national part |
| 4 | PT NIF with a bad check digit | Field error |
| 5 | DE company, well-formed VAT number, VIES says valid | `valid`, `vies_checked_at` set; the card shows "verified" |
| 6 | VIES says not valid | Saved as `invalid`; the card says VAT will be charged |
| 7 | VIES times out, or answers `MS_UNAVAILABLE`, 5xx or 429 | Saved as `pending`, next try in 15 min; nothing refused |
| 8 | DE tax id that is not a well-formed DE VAT number | Saved as `invalid`; VIES not called |
| 9 | GR entity, id typed "EL…" or bare | VIES gets `EL`; a "GR" prefix is accepted as the same country |
| 10 | FR billing country with an "ES…" id | Field error (prefix of another country) |
| 11 | GB, CH or US entity, or AE on the ae site | `not_applicable`; VIES not called |
| 12 | AE VAT id "10012345678901" (14 digits) or with letters | Field error; 15 digits saved |
| 13 | Job: a pending entity becomes due and VIES answers valid | `valid`; attempts reset |
| 14 | Job: VIES is still down | Next try 1 h after the 2nd failure, then every 6 h |
| 15 | Job: pending for over 3 days | One error log with the entity id only; not repeated on later runs |
| 16 | Job and edit race: the tax id changed while VIES was answering | The stale answer is discarded |
| 17 | Edit changes the tax id or the country | Status reset and checked again |
| 18 | Edit changes only the address | Status kept; VIES not called |
| 19 | Archived pending entity | The job skips it |
| 20 | Forged `vies_status` in the POST | Ignored |
| 21 | `price_order_for(user, lines, vies=fiscal.vies_status(entity))` for an IE-billed DE company | Reverse-charge text only when `valid`; `None` gives `not_applicable` |
| 22 | Migration on existing rows | EU rows `pending` and due now; others `not_applicable`; a pre-6.6 id failing the checks is marked "Please check the tax id" and stays usable |
| 23 | Any check | No tax id in logs, records or exception text |
| 24 | ES interface | All new strings translated |

</frozen-after-approval>

## Code Map

- `accounts/models.py:321-396` `FiscalEntity` (encrypted `tax_id`/`vat_id` max 40; `normalize_fiscal_id` :310; indexes filled in `save()` :387-396).
  - Add `vies_status` (choices `catalogue.pricing.VIES_STATUSES`, default `not_applicable`), `vies_checked_at`, `vies_pending_since`, `vies_attempts`, `vies_next_try_at` (indexed) and `vies_alerted_at`.
  - Migration `accounts` 0020, schema plus data (matrix row 22). 6.7 takes 0021.
- `accounts/fiscal.py`: `VAT_COUNTRY='AE'` :46; `FIELDS` :48 (unchanged); `clean_data` :77; `problems` :104-125 (new codes `tax_id_invalid`, `tax_id_kind`, `tax_id_prefix`, `vat_id_invalid`); `create` :160; `update` :183; `is_usable` :288.
  - New: `vies_status(entity_or_none)`, `retry_due(now)`, and a `tax_id_problem(entity)` read for the card.
- New `accounts/taxid.py`: pure functions on stdnum (`es.dni`, `es.nie`, `es.cif`, `pt.nif`, `eu.vat`), the EU-27 list with `GR→EL`, prefix splitting, and the AE 15-digit rule.
- New `accounts/vies.py`: module-level `fetch_vies(country, number)` → `VALID`, `INVALID` or `UNAVAILABLE`. It never logs the number. Add `VIES_URL` to settings with `config()`.
- `accounts/http.py`: add a JSON POST helper beside `post_form`; `FETCH_ERRORS`.
- Fail-safe pattern to copy: `signup/captcha.py:47`. Timeout: `settings.EXTERNAL_CHECK_TIMEOUT` :558.
- `accounts/forms.py:479` `error_messages`; `:542-573` `clean` and `add_service_problems`.
- `accounts/templates/accounts/settings_fiscal.html`: the status line on the card.
- Resolver, unchanged:
  - `catalogue/pricing.py:81-83` (constants), `:216` (`price_order(..., *, vies)`), `:339-341` (reverse charge);
  - `subscriptions/service.py:237,248` (`price_order_for`).
- Tests:
  - `conftest.py:86-105` `no_network`; `:110` `FakeExternalChecks` → a new autouse `FakeVies` (default valid, settable to invalid or unavailable).
  - `accounts/tests/test_fiscal.py`: sample ids like "es b-1234 5678" must become valid ids.
- Job:
  - `deploy/jobs.conf` line `viesretry | retry_vies | *-*-* *:00/15:00 UTC`.
  - Command pattern: `integration/management/commands/pull_config.py`.
  - jobs.conf test pattern: `regions/tests/test_geoip.py:488`.
- i18n: `mytp_publicsite/tests/test_i18n.py:131-170,185-200`, plus `.po` and `compilemessages`.

## Tasks & Acceptance

1. `requirements.txt`: pin `python-stdnum` and install it into `../.venv`.
2. `accounts/taxid.py`: the checks in Decisions, plus unit tests for each country rule (matrix rows 1-4, 8-12).
3. `accounts/http.py` and `accounts/vies.py`: the REST client and its mapping of unavailability to `UNAVAILABLE`; `VIES_URL` in settings; the `FakeVies` fixture in `conftest.py`.
4. `accounts/models.py` and migration 0020: the fields and the data step (row 22).
5. `accounts/fiscal.py`:
   - `problems` gets the new codes.
   - `create` and `update` call VIES outside the lock, only when the tax id or country is new or changed, and write the status under the lock.
   - Add `vies_status()` and the backoff and alert helpers.
6. `accounts/management/commands/retry_vies.py` and the `deploy/jobs.conf` line: matrix rows 13-16 and 19.
7. `accounts/forms.py`, `settings_fiscal.html` and the ES `.po` and `.mo`: messages and the card status lines (rows 5-7, 22, 24).
8. Docs:
   - README fiscal section: checks, VIES and the job.
   - deferred-work.md: re-check VIES at checkout (epic-payments).
   - epic-payments Notes: pass `fiscal.vies_status(entity)` to `price_order_for`.
9. Tests:
   - Every matrix row, in `accounts/tests/test_fiscal.py` plus new `test_taxid.py` and `test_vies.py`.
   - Fix the existing sample ids.
   - A resolver test for row 21.
   - Full suite with 0 failures; `makemigrations --check` clean.

**Acceptance:** the ticket's verify, plus matrix rows 1-24 covered by tests.

## Design Notes

ES labels:
- "Número de IVA verificado (VIES)"
- "No encontrado en VIES: se aplicará el IVA"
- "Verificación VIES pendiente"
- "Revise la identificación fiscal"
- Field errors: "El NIF no es válido.", "Para una empresa, indique un CIF." / "Para una persona, indique un DNI o NIE."

## Implementation Notes

Done as planned (Sonnet implementer, 2026-11-17).
- **Dependency and settings:** `python-stdnum==2.2` pinned; `VIES_URL` added to settings.
- **New modules:** `accounts/taxid.py` holds the offline checks and `vies_plan`. `accounts/vies.py` holds `fetch_vies`; `post_json` was added to `accounts/http.py`.
- **Migration** `accounts` 0020 `fiscal_vies`: six `vies_*` fields, plus a data step with its own copy of the EU-27 list.
- **Service:** `fiscal.py` has the new `problems` codes, VIES called outside the lock, and `vies_status` / `tax_id_problem` / `retry_due`.
- **Job:** the `retry_vies` command, with `viesretry` every 15 minutes in `deploy/jobs.conf`.
- **Pages:** the card status lines and 9 ES strings.
- **Docs:** README; deferred-work (VIES re-check at checkout); epic-payments Notes handoff.

Choices within the plan:
- A malformed id on a pending row becomes `invalid` in the job without calling VIES (this covers pre-6.6 rows).
- Any HTTP 4xx other than 429, and unknown error codes, count as unavailable (the module docstring says so). A wrong `VIES_URL` therefore surfaces as the 3-day alert.
- `RETRY_BATCH = 100` per run, oldest due first.
- "Please check the tax id" is computed at display time; no flag is stored.

The real VIES REST answer shape was confirmed against the live service on 2026-11-17. A success is HTTP 200 with `valid: true/false`. A rejected input is HTTP 200 with `actionSucceed: false` and `errorWrappers[{error: INVALID_INPUT}]`. A server fault is HTTP 500 with `VOW-ERR-1`.

## Plan Change Log

## Review Triage Log

Quick lens with a security focus (Opus reviewer, 2026-11-17). 0 high, 1 medium, 7 low.

Focus points 1-5 and 7-11 are ok: no ids in logs, no network call under a lock, stale answers discarded, the `vies_*` fields not writable, unavailability never `invalid`, the migration, backoff and alert, jobs.conf, stdnum licence (LGPL-2.1+, used unmodified), translations. Points 6 and 12 needed patches (findings 1 and 2).

| # | Finding | Severity | Verdict | Resolution |
|---|---|---|---|---|
| 1 | A foreign-prefix refusal hit free-text EU ids, e.g. an Italian codice fiscale `PLLMRA85T10A562S` for IT | medium | patch | Refused only when the id is a well-formed VAT number of the other country; row 10 still refused; IT/NL examples tested |
| 2 | The row 21 test went through `resolver.price_order`, not `price_order_for`, with an unsaved entity | low | patch | Moved to `subscriptions/tests/test_service.py` through `price_order_for` with a saved DE company entity |
| 3 | 4xx answers and unknown error codes become `pending`. The live API answers `INVALID_INPUT` with HTTP 200 and no `valid` key, which retried for 3 days | low | patch | `INVALID_INPUT` maps to `invalid`; other 4xx stay unavailable, documented; tests for both bodies |
| 4 | ES person NIFs starting with K, L or M (minors, non-residents) are refused | low | defer | The plan's decision says "DNI-NIF or NIE"; the user was asked whether to accept them |
| 5 | VIES was called before the 10-entity limit check | low | patch | An unlocked count check runs first; the locked check stays |
| 6 | The `RETRY_BATCH` limit was untested | low | patch | Test added |
| 7 | The migration test was weaker than row 22 | low | patch | It now asserts due-now, `vies_pending_since` set, and archived untouched |
| 8 | The `problems` docstring lacked the new codes | low | patch | Updated |

After the patches, my own run: 3509 passed, 12 skipped, 0 failed (SQLite); `makemigrations --check` reports no changes.

## Verification

**Commands:**
- the full suite (see local-dev notes) -- expected: 0 failures
- `python manage.py makemigrations --check` -- expected: no changes

## Staging

2026-11-17, run by Claude with the user's delegation and write-SSH OK for dev and dev-ae. The page and service checks ran in one transaction per site that was rolled back, so nothing was kept. Check 7 (by eye) is the user's.
- Portal PR #109 merged (b157f4e). The PR's `test` checks passed on PostgreSQL ×2 (runs 44050693241, 44050734996). Master run 44055334524 passed test plus both deploys. Local suite: 3509 passed, 12 skipped, 0 failed.
- 1: `/healthz` ok on dev and dev-ae; `accounts` 0020_fiscal_vies applied on both; python-stdnum 2.2 installed. Both sites were re-provisioned from merged `deploy/`, so the `mytp-<site>-job@viesretry` timers exist (every 15 minutes).
- 2: a live VIES call from dev and from dev-ae answers (SAP SE's public DE VAT number: valid).
- 3 (dev): an ES person with an invalid NIF is refused on the add page with the field error; nothing is stored.
- 4 (dev): a DE company with SAP SE's VAT number was saved as `valid` with the check time set, and the card shows "VAT number verified (VIES)".
- 5 (dev): the entity was forced to `pending` and due; `retry_due` checked it and set it `valid`.
- 6 (dev, doctor country set to DE inside the transaction): `price_order_for` gave 0.00 % with the reverse-charge text for `valid`, and 23.00 % with no text for `invalid` and `pending`.
- The migration found no EU rows to mark pending on either site.
- 7 (user): the fiscal list card status lines by eye, EN and ES. Not reported yet.

Decision (user, 2026-11-17, after the merge): a Spanish person may also give an NIF starting with K, L or M. This closes the deferred-work item, and the change ships with entry 7's PR.
