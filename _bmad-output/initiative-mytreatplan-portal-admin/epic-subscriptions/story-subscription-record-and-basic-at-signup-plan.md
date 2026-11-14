---
title: 'Subscription record and Basic at signup'
type: 'feature'
ticket: '1'
created: '2026-11-14'
status: 'done'
baseline_revision: '7839a25736fd1e0b5b19f7b3fddad663201297f0'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The portal knows the catalogue's subscriptions but not which one a doctor holds: every resolver and pricing function expects the caller to pass the subscription. Signup assigns nothing, so nothing can price for a doctor, and the later entries of this epic (screens, purchase, renewals, the admin assignment) have no record to change.

**Approach:** Add a `subscriptions` app to the portal with the doctor's subscription record (one current record per doctor, plus one row per change) and one service module every caller uses (AD-15). The service gives a doctor Basic at signup, answers the single "effective tier and can the doctor switch on their own" rule, prices and lists offers for a doctor through the existing resolver and pricing function, and changes the record only after checking its version. Existing doctors get a Basic record.

## Boundaries & Constraints

**Always:**
- The service module is the only writer of the record; every change checks the record's version, raises it by one, stores who made it (doctor, admin or system, with a UUID; AD-22) and adds a change row.
- The effective-tier rule lives in one function: active and past due count as the record's tier; cancelled counts as the record's tier up to and including the end date and as Basic after it; expired counts as Basic; a doctor without a record counts as Basic.
- A manual record refuses a change made by a doctor, in the service.
- The record refers to the catalogue subscription by its UUID only (no database link); a snapshot apply never deletes, blocks on or changes a doctor's record.
- Signup never fails because of the catalogue (no snapshot, no group, no Basic for the group).
- Only doctors get a record (not staff or superusers).
- Pricing arithmetic stays in `catalogue` (AD-16); the service only chooses the subscription and the country and calls it.

**Never:**
- No screen, reminder, purchase, voucher redemption, renewal, charge, cancellation, expiry job or admin action (entries 2–10).
- No outbox event and no client-record field (entry 10).
- No change to the catalogue tables, the snapshot contract, mytp-core or the admin site.
- No change to what the existing resolver functions, `price_order`, `price_subscription` and `price_catalogue` return for today's calls (a new optional argument is allowed).

## Decisions

- Decision (user, 2026-11-14): a Basic doctor's record holds the level only; their subscription is whichever active standard Basic their country group has today.
- Decision (user, 2026-11-14): a doctor whose country has no group or no standard Basic here (another region's country, a blank country, no snapshot yet) still signs up, is on Basic and is offered nothing until a group serves them.
- Decision (user, 2026-11-14): besides the current record, one row is kept per change (when, who, why, the values before and after).
- Decision (user, 2026-11-14): a doctor who holds a paid or manual subscription that the admin site deactivates keeps it until its end date; deactivated means not sold any more.
- Decision (user, 2026-11-14): for the staging checks the user signs up one doctor on dev after the deploy; the other checks, the merge and the staging run are delegated.
- Decision (2026-11-14, told to the user): one record per doctor; staff and superusers get none; the country group comes from the doctor's profile country until fiscal entities exist (5.12's decision); only the ticket's fields now; existing doctors get Basic by a migration plus a re-runnable command; no event to the admin site in this entry.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Signup, ES doctor, snapshot applied | Iberia has an active standard Basic | Record: Basic, active, no subscription id, no end date, no period, auto-renew off, not manual, version 1, started today; one change row `signup` by the doctor; effective = Basic with Iberia's Basic row; can switch | None |
| Signup with no snapshot / other region's country / blank country / group without a Basic | `resolver.subscription(country, 'basic')` is None | Same record; effective = Basic with no subscription; offers `[]`; pricing an order refused as the pricing function already does | Signup succeeds |
| The group's Basic is replaced on the admin site | new standard Basic row after a pull | The doctor is priced with the new row; the record is untouched | None |
| The doctor's country changes | ES → DE | Basic follows the new country's group | None |
| Staff or superuser | created by any path | No record | None |
| Doctor created by the Django admin | new non-staff user, blank country | Basic record; effective Basic with no subscription | None |
| Existing doctors | before the migration | After it each doctor has a Basic record (started on the day they joined) and a `backfill` change row by the system; staff none; running the command afterwards changes nothing | None |
| Doctor created by the previous release during the deploy | no record | Counts as Basic; the command gives them a record | None |
| Paid, active | Advanced, active, row active | effective = Advanced with that row; can switch | None |
| Paid, past due | Advanced, past due | effective = Advanced (grace) | None |
| Paid, cancelled | end date today or later / before today | Advanced / Basic with the group's Basic | None |
| Paid, expired | status expired | Basic with the group's Basic | None |
| Held row deactivated | Advanced, active; catalogue row inactive | effective = Advanced with that row; offers and order prices as if it were active | None |
| Held row missing from the snapshot | subscription id not found | Tier as recorded, priced with the group's Basic; an error is logged | None |
| Manual record | manual, active | effective = its tier and row; cannot switch; a change by a doctor refused; a change by admin or system applied | `ManualLocked` |
| Assign a manual subscription | admin or system actor, a manual catalogue row | Record takes the row's tier and id, manual on, auto-renew off, optional end date; change row | Refused for a doctor actor or a standard row |
| Stale version | expected version ≠ the record's | Nothing changes | `StaleVersion` with the current version |
| Two changes at once | same expected version | One applies, the other is stale (row lock) | `StaleVersion` |
| Snapshot apply | any | Records and change rows untouched; apply succeeds whatever ids the records hold | None |
| Doctor erased and purged | purge job | Record and change rows go with the user | None |

</frozen-after-approval>

## Code Map

- `subscriptions/` (new app; ARCH structural seed "one app per capability") -- `apps.py`, `models.py`, `service.py`, `migrations/`, `management/commands/`, `tests/`.
- `mytreatplan/settings.py:368-416` -- `INSTALLED_APPS`, the new app after `catalogue`, with its comment.
- `signup/views.py:404-481` -- `_create_account`: the one place a doctor is created; give Basic right after `client_sync.emit(user)` (`:434`), inside the atomic block.
- `accounts/admin.py:211-217` -- `save_model`: the Django admin's add; same call for a new non-staff user.
- `accounts/client_sync.py:39-41` -- `is_client`: the "doctors only" test to reuse.
- `accounts/erasure.py:128-152` -- the purge deletes users through a queryset: the record's link to the user must be CASCADE.
- `catalogue/resolver.py:210-223, 306-324` -- `country_group`, `subscription(country, tier)`, `subscription_by_id`: what the service reads; `:473-501` `offers_under` returns `[]` for an inactive subscription.
- `catalogue/pricing.py:213-246` -- `price_order` calls `offers_under`; takes the subscription from the caller.
- `catalogue/applier.py:184-222` -- every apply deletes and re-creates the catalogue rows with the admin site's UUIDs: no FK may point at them.
- `integration/sample.py:53-73`, `integration/remote.py:185-194` -- the version check to copy (`StaleVersion`, `RecordGone`).
- `storage/service.py:98-102`, `integration/models.py:233-236` -- actor type and UUID precedents (AD-22).
- `integration/tests/test_snapshot_tables.py` -- lists every snapshot-fed model and their reverse relations; forbids `tests` imports from production code.
- `mytp_publicsite/tests/test_cross_sites.py:35-42` -- checks every `CROSSES_SITES` model.
- `integration/tests/snapshot_rows.py`, root `conftest.py` (`admin`, `interface_settings`) -- how tests get catalogue rows.
- `signup/tests/helpers.py`, `accounts/tests/conftest.py:32-42` -- about 215 tests sign a doctor up with no snapshot applied; they must keep passing unchanged.
- `accounts/management/commands/sync_clients.py` -- the model for a re-runnable post-deploy command.
- `deploy/release.sh:189-201` -- migrate runs before the switch; the previous release keeps creating doctors until then.
- `README.md`, `deploy/README.md` -- the service, the command, the staging checks.

## Tasks & Acceptance

**Execution:**
- [x] `subscriptions/models.py`, `apps.py`, `migrations/0001_initial.py`, `mytreatplan/settings.py` -- `DoctorSubscription`: UUID primary key, `CROSSES_SITES = True`, `doctor` one-to-one to the user (CASCADE, `related_name='subscription_record'`), `tier` (the catalogue's tiers), `subscription_id` (plain `UUIDField`, null), `started_on`, `ends_on` (null), `billing_period` ('' / monthly / upfront), `auto_renew`, `status` (active, cancelled, past_due, expired), `manual`, `version` (`default=1, db_default=1`), `actor_type`, `actor_uuid` (null for the system), `created_at`, `updated_at`. `SubscriptionChange`: UUID primary key, FK to the record (CASCADE), `at`, `actor_type`, `actor_uuid`, `reason` (a short code), `before` and `after` (JSON of the record's business fields; `before` empty on creation), `version` (the record's after the change); append-only (an update or an instance delete raises). Neither is a `SnapshotFedModel` nor registered in the Django admin.
- [x] `catalogue/resolver.py`, `catalogue/pricing.py` -- a keyword-only `held=False` on `offers_under` and `price_order`: when true, an inactive subscription offers and prices as an active one. Defaults keep today's behaviour; `price_subscription` (a purchase) is not touched.
- [x] `subscriptions/service.py` -- the only writer. `ensure_basic(user, actor)`: creates the Basic record and its `signup` (or given reason) change row when the user is a doctor without one; does nothing otherwise; reads no catalogue table; must run inside a transaction. `effective(user, day=None)`: returns tier, catalogue subscription (or None), `held` (the record's own row rather than the group's Basic), `manual`, `can_self_switch`, per the matrix; reads only. `offers_for(user, day=None)` and `price_order_for(user, lines, *, vies, voucher=None, day=None)`: call the resolver and `price_order` with the doctor's profile country, effective subscription, `held` and uid. `change(user, *, expected_version, actor, reason, **fields)`: atomic, row lock, `StaleVersion` on a different version, `ManualLocked` when the record is manual and the actor is a doctor, validates the fields (tier, status, period, a subscription id that exists in the catalogue and matches the tier, end date not before start), applies, version + 1, change row. `assign_manual(user, subscription, *, expected_version, actor, ends_on=None)`: admin or system actor only, a manual catalogue row only; sets manual, auto-renew off. Actors: doctor (the user's uid), admin (a UUID), system (none).
- [x] `signup/views.py`, `accounts/admin.py` -- call `ensure_basic` after `client_sync.emit` inside the existing atomic block (signup: actor the doctor; admin add: actor system), for doctors only.
- [x] `subscriptions/migrations/0002_basic_for_existing_doctors.py`, `subscriptions/management/commands/assign_basic.py` -- the migration gives every non-staff, non-superuser user without a record a Basic record (started on the day they joined, Madrid date) and a `backfill` change row by the system, with the historical models, reading no catalogue table; reversible as a no-op. The command does the same through the service, `--dry-run`, prints how many were given a record; re-running changes nothing.
- [x] `subscriptions/management/commands/doctor_subscription.py` -- `doctor_subscription <uid or email>`: prints the record, the effective tier, subscription code, manual and can-switch; `--offers` lists what the doctor is offered with prices; `--order --product <UUID or code> [--quantity] [--vies]` prices one line through `price_order_for`. Read-only.
- [x] `subscriptions/tests/` -- every matrix row; the structural ones: an apply with records holding unknown ids succeeds and leaves them; no reverse relation appears on `catalogue.Subscription`; the purge removes the record; signup tests without a snapshot still pass unchanged; a test that nothing outside `subscriptions/service.py` and the migration writes the two models (the pattern of `catalogue/tests/test_vouchers.py::test_no_write_path_outside_the_snapshot_apply`); two concurrent changes (PostgreSQL-only, creating its own rows).
- [x] `README.md`, `deploy/README.md` -- the service's functions and rule; `assign_basic` as a post-deploy step; `doctor_subscription` usage.

**Acceptance Criteria:**
- Given an applied snapshot with a standard Basic for Iberia, when an ES doctor completes signup, then they have a Basic record at version 1 with a `signup` change row and `price_order_for` prices a TPS product under Iberia's Basic.
- Given no snapshot, when a doctor completes signup, then signup succeeds, the record exists and `offers_for` is empty.
- Given existing doctors and staff, when the migrations run, then every doctor has one Basic record and no staff user has one; `assign_basic` afterwards reports 0.
- Given a manual record, when the service is asked for a change by a doctor actor, then `ManualLocked` is raised and the record and its version are unchanged.
- Given a record at version 3, when a change arrives with expected version 2, then `StaleVersion` carries 3 and nothing is written.
- Given a doctor holding an Advanced row that is then deactivated, when their offers are listed, then they are the same as before the deactivation.
- Given any records, when a snapshot is applied, then the apply succeeds and the records are unchanged.
- The existing catalogue, signup and accounts tests pass without edits to their assertions.

## Implementation Notes

Built (portal only, no commit):

- `subscriptions/` app (after `catalogue` in `INSTALLED_APPS`): `models.py`
  (`DoctorSubscription`, `SubscriptionChange`, both `CROSSES_SITES` with a UUID key, the
  record's `tier` choices from `catalogue.models.Subscription.TIERS`, an `AppendOnly`
  guard on the change rows: instance `save` of an existing row, instance `delete` and a
  queryset `update` raise), `migrations/0001_initial.py`, `0002_basic_for_existing_doctors.py`
  (historical models, batches of 500, Madrid date of `date_joined`, reverse no-op),
  `service.py`, the commands `assign_basic` and `doctor_subscription`, `tests/`.
- `catalogue/resolver.py` `offers_under(..., *, held=False)` and `catalogue/pricing.py`
  `price_order(..., held=False)`; defaults unchanged, `price_subscription` untouched.
- `signup/views.py` `_create_account`: `ensure_basic(user, doctor_actor(user))` right after
  `client_sync.emit(user)`. `accounts/admin.py` `save_model`: `ensure_basic(obj,
  SYSTEM_ACTOR)` on add only (staff are skipped inside `ensure_basic`).
- `README.md` ("A doctor's subscription"), `deploy/README.md` (summary table row and
  "Doctor subscriptions": `assign_basic` after a deploy, `doctor_subscription`, the
  staging checks).

Choices the plan left open:

1. Actors are a small `Actor(type, uuid)` dataclass: `doctor_actor(user)`,
   `admin_actor(uuid)`, `SYSTEM_ACTOR`; an unknown type, a system actor with a UUID or a
   doctor/admin without one is `ChangeRefused`.
2. Validation failures raise `service.ChangeRefused` (a `ValueError`); a doctor without a
   record gets `RecordGone` (reused from `integration.remote`, as `StaleVersion`).
3. `change` rules beyond the listed ones: a standard Basic must have no subscription id;
   any other tier, or a manual record, must name one; the row's `manual` flag must match
   the record's; a doctor actor may not set `manual` at all. `started_on` / `ends_on`
   must be dates.
4. `assign_manual` also sets status `active`, billing period '' and `started_on` today,
   and refuses a deactivated manual row (deactivated = not sold any more).
5. `effective` uses the held row only while the record counts as its own tier, so a
   cancelled-and-past manual Basic falls back to the group's Basic. `effective.manual`
   is the record's flag whatever the day; `can_self_switch` follows the review patch
   below (False only while a manual record counts).
6. `price_order_for` without any subscription (no group, or a group without Basic)
   refuses in the service, since `price_order` cannot take None: the vies check, then
   `no_lines`, then `country_not_served` (no group) or `product_not_offered` (group
   without Basic) — the same reasons `price_order` gives.
7. `ensure_basic` creates inside a savepoint and returns None on an `IntegrityError`
   (a concurrent `assign_basic` / signup won the one-to-one), so the caller's transaction
   is not broken. It takes `reason` and `started_on` keywords for the backfill command.
8. Two read helpers in the service, `record_of(user)` and `doctors_without_a_record()`,
   so no module outside `subscriptions/models.py` and `service.py` names the models
   or the `subscription_record` relation (the structural test checks this by AST over
   every project package, tests and migrations excluded).
9. `doctor_subscription` finds a product by UUID, else by its exact code (two products
   with the code is an error); `--vies` takes a status (default `not_applicable`).
10. A missing held row is logged with `logger.error` (Sentry's default logging
    integration reports it).

Review patches (2026-11-14, the coordinator's review):

- A held row whose tier or kind (standard or manual) no longer matches the record is
  treated as a missing one: a distinct `logger.error` ("... is now <kind> <tier>, the
  record ..."), tier as recorded, priced with the group's Basic, `held` False.
- A user who is not a client (`accounts.client_sync.is_client`) has no record for the
  service: `record_of` and `effective` read none, `change` / `assign_manual` raise
  `RecordGone` (the account is re-read inside the lock); the row is kept, never deleted.
  `doctor_subscription` prints `record: none (staff account: any record is ignored)`.
  README says a staff account's record is ignored.
- The manual lock applies only while the record counts (`_counts`, same rule, today):
  `can_self_switch` is False and `ManualLocked` is raised only then. An expired manual
  record, or a cancelled one past its end date, is Basic and the doctor may switch; a
  doctor's change of `tier` or `subscription_id` on such a record sets `manual` False
  (a doctor may still never set `manual` to True, and the standard/manual row check
  then refuses a manual row).
- `_check` refuses `cancelled` without `ends_on`.
- `ensure_basic` returns None on an `IntegrityError` only when the doctor now has a
  record, else re-raises; the pre-check is a small `_has_record` helper (the race test
  patches it).
- `price_order_for` without a subscription now refuses in `price_order`'s exact order:
  vies check, `no_lines`, `country_not_served`, `no_billing_rules`, then
  `product_not_offered`.
- `SubscriptionChange` docstring states exactly what is guarded. The structure scan now
  also matches the model and table names in string constants, covers other apps'
  `migrations/` folders, and the backfill-only test matches `RunSQL` as well as
  `RunPython`.
- `deploy/README.md`: rolling back past this release needs `manage.py migrate
  subscriptions zero` first.

Tests (after the review patches): `subscriptions/tests/` 78 (77 pass on SQLite; the
concurrent-change test is PostgreSQL-only and skipped). Full suite (SQLite, browser tests
included): 2971 passed, 9 skipped, 3 failed (the known PostgreSQL-only tests in
`accounts/tests/test_crypto.py`). `pytest.ini` `testpaths` lists `subscriptions`
(without it the new tests were not collected). `makemigrations --check --dry-run`: no
changes. Before the patches: 59 tests, 2952 passed.

## Plan Change Log

## Review Triage Log

Quick lens (Opus), 2026-11-14: 10 findings, 9 patched, 1 rejected. Matrix audit: every row implemented and tested after the patches.

| # | Severity | Finding | Verdict |
|---|----------|---------|---------|
| 1 | medium | `effective` used a held row whose tier or kind was edited on the admin site (Premium discounts on an Advanced record) | patched: treated as a missing row (tier as recorded, the group's Basic, error logged) |
| 2 | medium | A user added in the Django admin and then made staff, or a doctor made staff, kept a record | patched: a record of a staff account is ignored; the row is kept |
| 3 | medium | A manual record stayed locked after it expired or was cancelled past its end date | patched: the lock holds only while the record counts; a doctor's change then clears `manual` (told to the user) |
| 4 | low | `change` accepted `cancelled` without an end date (Basic the same day) | patched: refused |
| 5 | low | `ensure_basic` swallowed every `IntegrityError` | patched: only when the doctor now has a record |
| 6 | low | The previous release does not know the tables: after a rollback a user delete fails on the foreign key | patched: `deploy/README.md` says to run `migrate subscriptions zero` first |
| 7 | low | `price_order_for` without a subscription raised `product_not_offered` where `price_order` raises `no_billing_rules` | patched: same order as `price_order` |
| 8 | low | `SubscriptionChange` docstring overstated the guard; the structural scan missed names in strings, other apps' migrations and `RunSQL` | patched |
| 9 | low | Tests missing for 1–5 | patched |
| 10 | low | The missing-row error is logged on every `effective()` call | rejected: Sentry groups it; rate-limit later if noisy |

## Design Notes

- Working notes for the implementer: the branch is `story/8.1-subscription-record`, already created in the portal checkout; do not create another branch and do not commit. Portal only: do not touch `../mytp-admin` or `../mytp-core`.
- Basic's record keeps `subscription_id` null; the group's Basic is read at each call through `resolver.subscription(user.country.code, 'basic')`.
- Dates are dates from `resolver.today()` (the catalogue's validity day).
- An active or past due record whose end date has passed still counts as its tier: moving the status at the end date belongs to entries 6, 7 and 9.
- `StaleVersion` is reused from `integration/remote.py` so entry 10's remote write maps it to 409 without a translation.
- `related_name='subscription_record'` is on the user, not on a catalogue model: `test_every_reverse_relation_is_listed` stays as it is.
- The one-rule concurrency lesson (5.9): a `django_db(transaction=True)` test creates its own rows.

## Verification

**Commands:**
- the repo's full suite command (SQLite, `../.venv`) -- expected: all passing except the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py`
- `../.venv/bin/python manage.py makemigrations --check --dry-run` -- expected: no changes

**Staging (after the deploy):**
1. `assign_basic --dry-run` on dev and dev-ae reports 0 to give (the migration did it); `doctor_subscription` for dev-ae's existing doctor shows Basic.
2. The user signs up one doctor on dev with country ES.
3. `doctor_subscription <email>` on dev: Basic, `SEED-BASIC-IBERIA`, can switch, version 1.
4. `doctor_subscription <email> --offers`: Iberia's Basic offers; `--order --product SEED-TPSB-MEDIUM-EUR`: priced by the Spanish company.
5. In a shell on dev with a system actor: `assign_manual` to a manual subscription if staging has one, else a change by a doctor actor on a record flagged manual in a rolled-back transaction: refused with `ManualLocked`.
6. `pull_config` on dev after the checks: `applied` or `unchanged`, records intact.

## Staging

- Merged 2026-11-14: portal PR #98 (merge 9777265). Portal only: no mytp-core release, no admin change. Migrations `subscriptions` 0001 and 0002.
- CI: both PR `test` checks `pass`; master run 43572234247 (test, deploy dev, deploy dev-ae) all `success`. Local suite on the merged branch: 2970 passed, 12 skipped, 0 failed.
- Checks on 2026-11-14, all passed:
  1. `assign_basic --dry-run` reports 0 on dev and dev-ae. dev had only a staff account (no record). dev-ae's two doctors have a Basic record from the migration (`backfill`): the AE doctor is priced with `SEED-BASIC-UAE` (7 AED offers), the IE doctor (another region's country) is on Basic with no subscription.
  2. The user signed up a doctor on dev with country ES (uid 965c9a59-f6cb-46ad-957d-49652812c9f7).
  3. `doctor_subscription <email>` on dev: Basic, active, started 2026-11-14, version 1, last change by the doctor; effective Basic with `SEED-BASIC-IBERIA`, can switch.
  4. `--offers`: ICE and the six TPS products in EUR. `--order --product SEED-TPSB-MEDIUM-EUR`: 249.00 EUR, MyTPDSO Spain S.L., VAT 0 % with the staging text, gateways redsys and bank_transfer.
  5. On dev-ae, in a transaction that was rolled back: a record flagged manual reads "cannot switch" and a change by the doctor is refused with `ManualLocked`; a change with a wrong version is refused with `StaleVersion`. Nothing was left behind.
  6. `pull_config` on dev and dev-ae: v204 applied, the records unchanged.
