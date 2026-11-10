---
title: 'Clone a product into another currency'
type: 'feature'
ticket: '7'
created: '2026-11-10'
status: 'done'
baseline_revision: 'd4eec7851c0e60392cf050942faac84b860e51b5'
route: 'full'
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

**Problem:** C6 (AD-6): a product has one currency, so selling it in USD or AED means typing it again and converting the price by hand; the company wants a clone that converts once, at a reference rate the editor can change.

**Approach:** On the admin site only: a daily job stores the ECB EUR→USD reference rate; a "Clone" action on a product opens the product form pre-filled with the source's fields, the target currency, the rate (USD from the stored rate, AED through the fixed 3.6725 USD peg) and the converted net price rounded to whole units; saving creates a separate product with its own fixed price. Nothing changes in mytp-core or the portals: a clone is one more product in the snapshot.

## Boundaries & Constraints

**Always:**
- Conversion happens only at clone time (AD-6); the clone's price is fixed afterwards and never follows a rate.
- The rate and the price are pre-filled and editable; the rate used is recorded in the audit entry of the clone.
- A clone is created through the same service as any product: unique code, audited, snapshot rebuilt.
- The job runs under `runjob` with its Sentry check-in (AD-8); outbound HTTP as elsewhere in the admin site (urllib, the shared timeout and user agent, no redirects), never called from a page request.

**Never:** no conversion on the portals or in mytp-core; no automatic repricing of clones; no rate for any pair but those decided below; no copy of cost links (they are in the source's currency).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clone into AED | a 249.00 EUR product, stored rate 1.0800 | rate pre-filled 3.9663 (1.0800 × 3.6725), price pre-filled 988 AED (987.61 rounded) | — |
| Clone into USD | the same product | rate 1.0800, price 269 USD | — |
| Rate edited | the editor types another rate | the price follows it in the form before saving | a rate of 0 or below refused |
| Later rate change | the stored rate changes after the clone | the clone's price is unchanged | — |
| Clone is inactive | a clone just saved | inactive: in the snapshot marked inactive, not offered, until an editor activates it | — |
| Source not EUR | a USD or AED product | no Clone action | the clone address answers 404 |
| Stale rate | the last stored rate is more than 7 days old | pre-filled, with its date and a note that it is old | — |
| No rate yet | nothing stored | the rate field is empty; the editor types one | saving without a rate refused |
| Code | source code `TPS-1`, target AED | pre-filled `TPS-1-AED`, editable | a taken code refused, as for any product |
| Source reference | a clone and its source | "Cloned from" on the clone's page, "Clones" on the source's | never in the snapshot |
| Job, new day | the ECB file of a new date | that date's USD rate stored once | — |
| Job, same date | weekend or TARGET holiday: the file repeats the last date | nothing stored, the job succeeds | — |
| Job, failure | download fails, or the file has no USD rate | the job fails (Sentry); the last rate stays | — |
| Rate history | stored rates | listed read-only, newest first, for whoever can view the catalogue | — |
| Portals | — | the clone reaches the portals as any product, once active offered | — |

## Decisions

- Decision (user, 2026-11-10): the rate source is the ECB's daily reference file (`eurofxref-daily.xml`), its URL a setting.
- Decision (user, 2026-11-10): the job runs every day at 16:30 UTC and stores the rate under the date the ECB gives; a repeated date stores nothing and is not a failure; a failed download or an unreadable file fails the job.
- Decision (user, 2026-11-10): the form pre-fills from the last stored rate and shows its date; a rate more than 7 days old is said to be old; with no rate stored the field is empty and the editor types one.
- Decision (user, 2026-11-10): the converted price is rounded half up to whole units; rates have 4 decimals; the AED rate is the USD rate × 3.6725, rounded to 4 decimals.
- Decision (user, 2026-11-10): clones go from a EUR product into USD or AED only.
- Decision (user, 2026-11-10): the clone copies texts, category, size, quantity range, calls, validity dates and notes, all editable in the form; the code is pre-filled as `<source code>-<currency>`; cost links are not copied. The clone is created inactive and stays so until an editor activates it.
- Decision (user, 2026-11-10): the clone keeps an admin-only reference to its source, shown on both product pages, never sent to the portals.
- Decision (user, 2026-11-10): every stored rate is kept and listed read-only on a page under the catalogue.
- Decision (user, 2026-11-10): whoever can change the catalogue clones (Commercial Manager, Sysadmin).

</frozen-after-approval>

## Code Map

- Repository: all the work is in the admin site, `/Users/jadeandres/Library/CloudStorage/GoogleDrive-mytpdso@gmail.com/Mi unidad/Desarrollo Portal MyTreatplan/code/mytp-admin` (the baseline above is its commit), on the branch `story/5.7-clone-product`, already checked out: work there, commit nothing, push nothing. This plan file lives in another checkout (the portal's planning worktree): do not edit anything else there. mytp-core (`../mytp-core`) and the portal (`../mytreatplan.com`) are read-only references. Run the admin suite as its README says, with the test venv `/private/tmp/claude-501/admin-venv`. The line numbers below predate small edits; search by name when one is off.

- Decisions already made: epic Notes :62 (ECB EUR→USD, AED through the 3.6725 peg, editable, whole units), :67 (prices fixed per currency, converted once on clone); AD-6 :128; AD-8 :140 (jobs).
- Admin jobs: `deploy/jobs.conf` (`name | command args | OnCalendar UTC [| sites]`), runner `mytp_core` `runjob` (Sentry monitor `mytp-<SITE>-<name>`); pattern `audit/management/commands/pruneaudit.py` with its logic in `audit/retention.py`, tests `audit/tests/test_audit.py:528`; `layout/tests/test_deploy.py:87-106` pins the job set and must be updated.
- Outbound HTTP: urllib with `EXTERNAL_CHECK_USER_AGENT` and `EXTERNAL_CHECK_TIMEOUT` (`mytp_admin/settings.py:145-146`), no redirects (`mytp_core/interface.py:555-565`); tests stub the opener (`interface/tests/test_ping.py:315`) and point base-URL settings at a host that cannot exist (`settings.py:141`). Settings via `config('X', default=...)`, documented in `deploy/staging.env.example`.
- Admin catalogue: `catalogue/models.py` `Product` :234-285 (docstring :189 names this entry), `AddOn.applies_to` :475 (add-ons follow category, size and currency: nothing to copy), `ProductCostLink` :483-532 (in the product's currency: not copied); `service.py` `create_product` :320, `_create` :200, `normal_code` :284; `views.py` `_detail` :696, `_create` :761, `_form_for` :737 (takes `initial`), `ProductForm` :377; `urls.py`; `templates/catalogue/detail.html:24-37`; `audit/registry.py:33-44` (`AUDITED_VIEWS`: a new change view must be listed); `permissions/matrix.py:81,108,115`.
- mytp-core `money.py:34` (`CURRENCIES`), :26 ("No currency conversion"): unchanged. Portal: unchanged.

## Tasks & Acceptance

**Execution:**
- [x] `catalogue/models.py`, a migration -- `ExchangeRate` (date unique, USD per EUR with 4 decimals, stored at) and `Product.cloned_from` (nullable, protected reference to the source) -- the stored rates and the source reference.
- [x] `catalogue/rates.py` (new) -- download and parse the ECB file (urllib, the shared timeout and user agent, no redirects, URL from a new setting `ECB_RATES_URL`), store the USD rate of its date once, the rate for a target currency (USD: stored; AED: × 3.6725, 4 decimals), the converted price (half up to whole units) -- the only place that converts.
- [x] `catalogue/management/commands/fetch_rates.py`, `deploy/jobs.conf`, `layout/tests/test_deploy.py` -- the daily job at 16:30 UTC; the pinned job set updated.
- [x] `catalogue/service.py` -- `clone_product(actor, source, *, currency, rate, ...)`: a EUR source only, target USD or AED, created inactive with `cloned_from`, the rate used in the audit entry, snapshot rebuilt; no cost links copied.
- [x] `catalogue/views.py`, `urls.py`, `templates/catalogue/` -- the Clone action on a EUR product's page (catalogue change), the pre-filled form with the target currency, rate (its date, the old-rate note) and price that follows the rate, "Cloned from" / "Clones" on the pages, the read-only rates page; `audit/registry.py` lists the new change view.
- [x] `catalogue/snapshot.py` -- unchanged output: check `cloned_from` is never sent (a test).
- [x] `mytp_admin/settings.py`, `deploy/staging.env.example`, `README.md` -- the setting, the job, the runbook section with the staging check.
- [x] Tests for every row of the matrix; the download is stubbed, never real.

**Acceptance Criteria:**
- Given a stored EUR→USD rate, when a 249 EUR product is cloned into AED, then the form pre-fills the rate from that rate and the peg and a whole-number price, and saving creates a separate, inactive AED product.
- Given a clone, when the stored rate changes later, then the clone's price is unchanged.
- Given the ECB file of a day, when the job runs, then that day's USD rate is stored once; when the download fails, then the job fails and the last rate stays.

## Implementation Notes

- Admin site only (mytp-admin, branch `story/5.7-clone-product`); nothing changed in mytp-core or the portal. Suite on SQLite: 1322 passed (10 PostgreSQL-only skips).
- A EUR product's page has two actions, "Clone into USD" and "Clone into AED" (`/catalogue/products/<id>/clone/<USD|AED>/`); the form is the product form with the currency fixed.
- The rate used is in the clone's audit entry (`clone_rate`) and stored nowhere else. Stored rates are not audited: the `ExchangeRate` table is the history. A second file with an already stored date is ignored, even with another rate.
- The price follows the rate in the form through `catalogue/static/catalogue/clone.js` (exact integer arithmetic); its test runs under `node` and skips where node is absent (CI has none).
- The download refuses a URL that is not https, a document with a DOCTYPE or entity declaration and a body over 1 MB. "Old" is more than 7 days, in Madrid dates.
- A clone is activated with the existing Reactivate action (its audit action reads "reactivate").
- After review: a product that was cloned or has clones cannot change currency; a broken HTTP response is a `RatesError` like any failed download.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, 2026-11-10): 2 findings — 2 low, 0 false. No unmet acceptance criterion.

| # | Where | Finding | Verdict | Route | Evidence / action |
|---|-------|---------|---------|-------|-------------------|
| 1 | `catalogue/rates.py` `download` | an `http.client.HTTPException` (IncompleteRead) escapes as a raw traceback | low | patch | the rest of the code catches it (`mytp_core/interface.py`, `kms.py`); now a `RatesError`, tested |
| 2 | `catalogue/service.py` `update_product` | a clone could be edited to EUR and cloned again; a source with clones could leave EUR | low | patch | `currency` was editable after cloning; a product that was cloned or has clones now refuses a currency change, tested both ways |

## Verification

**Commands:**
- The admin suite — all pass; CI on PostgreSQL.

**Manual checks (staging):**
- Re-provision admindev (new job); run the job once: a rate is stored; a second run stores nothing. Clone a 249 EUR product into AED and into USD: rate and whole-number price pre-filled, the clones inactive; activate them: they reach dev (`price_catalogue --product <id>`). Disable the new Sentry monitor after its first run.

## Staging

Done 2026-11-10 (the user delegated the merge and the staging checks). mytp-admin PR #31 (48cc569; run 42854955830: test and deploy green; CI on PostgreSQL passes).

- admindev re-provisioned with the merged deploy files: the `fetchrates` timer is installed (next run 16:30 UTC). The job run once through its unit against the real ECB file: "Stored 2026-11-10: 1.1214 USD per EUR", success; a second run: "Nothing stored: 2026-11-10 is already stored".
- A 249.00 EUR product (STG57, TPS branded, medium, 3 calls) cloned through the service as the command actor `staging_check_5_7`: STG57-USD at rate 1.1214, 279.00 USD; STG57-AED at rate 4.1183 (1.1214 × 3.6725), 1025.00 AED; both inactive, with their source, size and calls, the rate in each audit entry. A clone of a clone and a currency change of a clone are refused.
- dev: the clones arrive inactive ("offered for new orders: no"); after activation on admindev they are active and offered.
- Not checked: the clone form in a browser (its pre-filled rate, date and price and the script that makes the price follow the rate rest on the suite; the script's test does not run in CI, which has no node). The Sentry monitor `mytp-admindev-fetchrates` is for the user to disable.
- Seen, for a later entry: an ES doctor's offers list the USD and AED clones next to the EUR product (prices 251.10 USD, 922.50 AED with the 10 % discount); which currency a doctor is offered comes with the billing rules (entry 9) and the full resolver (entry 12).
