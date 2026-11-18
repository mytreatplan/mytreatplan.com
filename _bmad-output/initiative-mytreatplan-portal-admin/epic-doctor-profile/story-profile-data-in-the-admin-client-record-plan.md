---
title: 'Profile data in the admin client record'
type: 'feature'
ticket: '9'
created: '2026-11-17'
status: 'done'
baseline_revision: 'fe494d179302750eda4d6c2600d50f252dedd356'
route: 'full'
route_source: 'pinned'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 1
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The admin site's client record (4.8) knows a doctor's personal data but not their fiscal entities (6.5, with 6.6's VIES status) or shipping addresses (6.7). AD-2 says both are region-owned and copied to the client record, encrypted with the admin's keys. Maker logins and the photo must never be copied.

**Approach:** Three repos, released in the order core → admin → portal.
- **mytp-core 0.28.0:** adds two optional lists to the `client.updated` contract, `fiscal_entities` and `shipping_addresses`. The fields are additive, and readers already ignore unknown fields.
- **Admin:** stores them as child rows of `ClientRecord`, encrypted, synced from each event and deleted on erasure.
- **Portal:** every fiscal-entity and shipping-address write, and every VIES status change made by the retry job, emits `client.updated` as a full snapshot (AD-18). Existing data is backfilled with `sync_clients`.

**Decisions (user, 2026-11-18):**
- **Admin page:** 6.9 stores the rows only; it builds no client page. The ticket's "appears in the client record" is checked on admindev from the stored rows. A handoff note in epic-sales-finance says what its client record page shows, and how it masks per role (the tax id masked to its last 4 characters for roles without fiscal duties).
- **Entities sent, and recovering archived ones for auditing:**
  - The snapshot carries the active entities and addresses, plus the archived ones with their `archived_at`, at most the 25 most recently archived per list (Decision (user, 2026-11-18, review): 100 → 25, so a field-maximal snapshot fits; orders keep their own snapshots).
  - The admin stores them all, and its `ClientRecord` exposes `active_*` and `archived_*` querysets.
  - The epic-sales-finance handoff asks its page to show active ones by default, with a "Show archived" filter for auditing.
  - (Claude's reading of "a, but with an option to recover the archived ones": the data must already be on the admin side for a future filter. Orders keep their own snapshots, so they do not depend on this.)
- **Fields:**
  - Fiscal: id, kind, first and last name, legal name, tax id, VAT id, the address lines, city, postal code, province, country, billing email, `is_default`, `used_at`, `archived_at`, `version`, `vies_status`, `vies_checked_at`.
  - Shipping: id, label, recipient name, clinic name, contact phone, delivery notes, the address fields, country, `is_default`, `used_at`, `archived_at`, `version`.
- **Text the central checks refuse (Decision (user, 2026-11-18, review)):** a fiscal or shipping field whose text the mytp-core payload checks refuse (`find_violations`: e.g. 200+ base64-like characters, a data: URI, an object-storage link) is refused at the form/service with a field error, as the personal-data page effectively does. Nothing is blanked or withheld in the payload.
- **Tax-id search on the admin site:** not now (deferred-work entry). Admin-side blind indexes come with the search page, and a re-sync fills them.
- **Which writes emit:**
  - every fiscal and shipping create, edit, default change, delete or archive, and `mark_used`;
  - the VIES job's status changes.

  Each emit happens inside the write's transaction, with the user row locked (the job's write takes the user lock first, then the entity).
- **Release and staging are delegated:**
  1. Tag core v0.28.0, check its CI, and vendor it into the admin and the portal.
  2. Merge the admin PR, then the portal PR.
  3. Run `sync_clients` on dev and dev-ae.
  4. On admindev, check that a test doctor's fiscal entity and address arrive encrypted, that an edit updates them, that an archive keeps the row with `archived_at`, and that a delete removes it. The dev writes are real, so the events flow, and the test rows are deleted afterwards.

  Sonnet implements across the three repos; Opus reviews with a security focus.

## Boundaries & Constraints

**Always:**
- The event stays a full snapshot with the per-client `sync_version`. The admin's stale-version and dedupe rules are unchanged.
- The new fields default to `None`, meaning "not sent" (an older portal), which leaves the stored rows untouched. An empty list means "none".
- The admin syncs children by id: it upserts what is sent and deletes what is missing, inside the event's transaction.
- Encrypted on the admin side: names, ids, address lines, city, postal code, province, emails, label, recipient, clinic, phone and notes. Plain: country, kind, flags, status, version and timestamps.
- No key starts with `patient` (`contracts/checks.py`). A full snapshot (10 + 10 active, 25 + 25 archived), every field at its maximum length with 2-byte characters, stays below `MAX_EVENT_BYTES`; a test proves it.
- A contract test proves that no maker-login or photo field can appear in the payload.

**Never:**
- No maker login, no photo and no VIES retry internals (attempts, next try, pending since, alerted).
- No editing on the admin side; the region owns the data. No admin page.
- No change to the portal pages.

## I/O & Edge-Case Matrix

| # | Situation | Expected |
|---|---|---|
| 1 | Doctor creates a fiscal entity on dev | `client.updated` with the list; admindev stores the row encrypted, keyed by the portal id |
| 2 | Edit, make default, `mark_used` | New event; the admin row is updated (version, default flag, used date) |
| 3 | Delete an unused entity | The next snapshot lacks it; the admin row is deleted |
| 4 | Delete a used entity (archived) | Sent with `archived_at`; the admin keeps the row, archived |
| 5 | 26 archived addresses | Only the 25 most recently archived are sent; the oldest admin row is deleted |
| 6 | Same rows 1-5 for shipping addresses | Same behaviour |
| 7 | VIES job turns pending into valid | Event emitted; the admin row shows `valid` and the check time |
| 8 | Event from an older portal without the lists (field absent) | Stored children untouched |
| 9 | Event with empty lists | All children deleted |
| 10 | Stale or duplicate event | Ignored as today; children unchanged |
| 11 | `client.erased` | ClientRecord and all children deleted |
| 12 | Any payload | No maker-login, photo or VIES retry keys (contract and portal tests) |
| 13 | Admin raw SQL | Encrypted columns are ciphertext |
| 14 | Writer with an extra key in an item | Refused by the writer (`extra='forbid'`); the reader ignores it |
| 15 | Largest snapshot | Below `MAX_EVENT_BYTES`; outbox accepts it |
| 16 | `sync_clients` after deploy | Every doctor's lists reach admindev |

</frozen-after-approval>

## Code Map

- **Core** (`../mytp-core`, v0.27.0, commit 6e55e1e):
  - `mytp_core/contracts/clients.py`: `:84` `ClientUpdated` (writer, `extra='forbid'`) and `:61` `ClientUpdatedReader` (`extra='ignore'`); parity test at `tests/test_contracts.py:678-682`.
  - Add the item models `FiscalEntityItem` and `ShippingAddressItem` (writer and reader variants), as `list[...] | None = None`. The nested-list pattern is at `catalogue.py:1195-1204`.
  - Regenerate the schema `mytp_core/schemas/payload_client_updated.json`.
  - README contract section ~:910 and changelog :1201 (0.26.0 is the precedent for additive fields).
  - Checks: `contracts/checks.py`; `events.py:44` `MAX_EVENT_BYTES`.
  - Release: bump `pyproject.toml:7`, tag v0.28.0, CI, then `scripts/vendor-mytp-core.sh v0.28.0` in each site (as for 5.17).
- **Admin** (`../mytp-admin`, 3a2dd50):
  - `clients/models.py:8` `ClientRecord`: add `ClientFiscalEntity` and `ClientShippingAddress` (pk = the portal UUID, FK to the record with CASCADE, `mytp_core.crypto` fields), plus the next `clients` migration.
  - `interface/events.py:99` `_apply_client_updated` (sync the children when the field is not `None`); `:117` `_apply_client_erased` (the cascade covers it; test it); `AggregateVersion` ~:190.
- **Portal:**
  - `accounts/client_sync.py:55` `payload_of` (add both lists); `:82` `emit(user)` (inside `atomic()`, locks the user).
  - Emit points in `accounts/fiscal.py`: `create`, `update`, `make_default`, `remove`, `mark_used`. The VIES job write `_write_check` must take the user lock first.
  - Emit points in `accounts/shipping.py`: `create`, `update`, `make_default`, `remove`, `mark_used`.
  - Do not emit from `erase`; erasure already emits `client.erased`.
  - Tests to change:
    - the no-event tests in `accounts/tests/test_fiscal.py` and `accounts/tests/test_shipping.py`;
    - `PAYLOAD_KEYS` in `accounts/tests/test_profile_photo.py:630`;
    - `accounts/tests/test_client_sync.py`.
  - The fiscal and shipping module docstrings that say "entry 9 does".
- **Architecture:** AD-2 :87; AD-18 :204.
- **Build setup (as for 5.17):**
  - Branch `story/6.9-client-record-profile-data` is already checked out in all three repos (portal = this repo; `../mytp-core`; `../mytp-admin`). Commit nothing, push nothing, tag nothing: the release is the main session's.
  - Core venv: `/private/tmp/claude-501/core-5.17-venv`, admin venv: `/private/tmp/claude-501/admin-5.17-venv` (reinstall the editable/vendored core into them as needed). Portal venv: `../.venv`, but do not install the local wheel there (it is shared); make `/private/tmp/claude-501/portal-6.9-venv` (copy of the portal requirements) for the portal suite with the local wheel.
  - Local wheel: copy the core working tree's tracked files (`git ls-files | rsync --files-from=-`) to a temp dir, `SOURCE_DATE_EPOCH` = core HEAD commit time, `pip wheel --no-deps`, put it in both sites' `vendor/` and move each `requirements.txt` line to 0.28.0 (remove the 0.27.0 wheel). The main session re-vendors from the tag with `scripts/vendor-mytp-core.sh v0.28.0` before the PRs.
  - Portal suite (repo root, sandbox off): `FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" DATABASE_URL="sqlite:///$TMPDIR/p69.sqlite3" <venv>/bin/python -m pytest -q -p no:cacheprovider --tb=line` (expected 0 failures; 12 skipped are PostgreSQL-only).
  - Admin suite (in `../mytp-admin`): `SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver DATABASE_URL=sqlite:///$TMPDIR/a69.sqlite3 FIELD_ENCRYPTION_KEYS=<the portal test key above> BLIND_INDEX_KEY=<the portal test key above> ALLOW_FAKE_VERIFICATION=True <venv>/bin/python -m pytest -q -p no:cacheprovider`.
  - Core suite (in `../mytp-core`): `<venv>/bin/python -m pytest -q -p no:cacheprovider`; regenerate schemas with the core's own script/test that checks them.
  - Previous baselines: core 1599 passed / 4 skipped, admin 1609 / 12, portal 3582 / 12.

## Tasks & Acceptance

1. **Core 0.28.0:** the item models, the optional lists on writer and reader, the schema, tests (parity; no maker or photo keys; the size of the largest snapshot), README and changelog.
2. **Admin:** the child models and migration, the sync in `_apply_client_updated`, the erasure cascade test, `active_*` and `archived_*` querysets, and tests for matrix rows 1-11 and 13 with crafted events. Vendor 0.28.0.
3. **Portal:** `payload_of` lists (active, plus the last 25 archived each); emits at every write point listed, including the VIES job with the user lock; test updates; and tests for rows 1-7, 12, 15 and 16. Vendor 0.28.0.
4. **Docs:**
   - handoff in the epic-sales-finance Notes (the page shows these lists, active by default with a "Show archived" filter, the tax id masked per role);
   - deferred-work entry for admin tax-id search;
   - README sections in the portal and the admin.
5. **Verification:** core, admin and portal suites with 0 failures; `makemigrations --check` clean in admin and portal.

**Acceptance:** the ticket's verify, plus matrix rows 1-16 covered by tests.

## Implementation Notes

- Only real changes emit: an update or default change that changes nothing, and a repeat `mark_used`, send no event; a VIES retry that stays pending sends nothing (only status or check-time changes do).
- The admin refuses a whole event whose item id belongs to another client (`invalid_payload`), so ids can't be taken over; since snapshots are full, later events of that doctor are refused too (documented in the admin README).
- The VIES job catches a failing entity (id and exception class logged), counts it as discarded and goes on, so one doctor can't block the others.
- Text the central checks refuse is a field error (`not_allowed`) in `fiscal.problems` / `shipping.problems` (user decision 2a); two old shipping tests changed their filler text from ASCII runs to accented letters.
- Release: mytp-core commit cf21dea, tag v0.28.0 (local until pushed); both sites vendored from the tag (wheel sha256 fa72481c…734c).
- Verified 2026-11-18 on the tagged wheel: core 1606 passed / 4 skipped; admin 1623 / 12; portal 3605 / 12; 0 failures; `makemigrations --check` clean in admin and portal; the new ES message renders.

## Plan Change Log

- 2026-11-18, review loop 1 (findings 1 and 3, intent_gap): the user chose 1a and 2a. Amended inside the frozen block with the user's decision: archived cap 100 → 25 per list (`MAX_ARCHIVED_SENT`, core 0.28.0 unreleased, so changed in place); the size test uses field-maximal 2-byte text; text refused by the central checks is a field error at the fiscal/shipping form and service, and `client_sync._central_text` (blanking) is removed. Known-bad state avoided: a snapshot over `MAX_EVENT_BYTES` that blocks every save of a doctor; silently blank fields on the admin copy. KEEP: everything else of the build and the patches for findings 2, 4, 5, 7 (code kept, not reverted, as the change is local).

## Review Triage Log

Quick lens (Opus, security focus), 2026-11-18. No AGENTS.md/CLAUDE.md in the three repos.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | A field-maximal snapshot (10+10 active, 100+100 archived) is ~413 KB ASCII / ~732 KB with 2-byte text, over `MAX_EVENT_BYTES` (256 KiB); the tests use typical values; when it happens every emitting write for that doctor fails (500) with no way out | medium | `integration/outbox.py` `record` raises `OutboxError` over the limit; the frozen Boundaries require the full snapshot to fit and a test to prove it; the 100 cap was Claude's reading of Q2 | intent_gap → user chose a (2026-11-18); patched per the amended plan |
| 2 | One failing emit in the VIES job aborts `retry_due`; the entity stays first in the due order, so retries and alerts stop for everyone | medium | `_write_check` now emits; `retry_due` has no per-entity handling; due order `vies_next_try_at, pk` | patch |
| 3 | Unplanned `client_sync._central_text` sends text refused by the central checks as `''`, so the admin copy looks genuinely empty | medium | not in the plan; the profile fields make the save fail instead; finance would audit a silently blank field | intent_gap → user chose a (2026-11-18); patched per the amended plan |
| 4 | `MAX_ACTIVE_SENT` not tied to `fiscal.MAX_ENTITIES` / `shipping.MAX_ADDRESSES` | low | contract caps a list at 110; raising a portal limit past 10 would break every save; fix is one test | patch |
| 5 | An id owned by another client refuses every later full snapshot for that doctor | low | by design (prevents a takeover); reachable only with a UUID collision or a faulty portal; fix is a docs sentence | patch (docs) |
| 6 | Concurrent insert of the same new child id by two clients' events → IntegrityError (500) instead of Refused | low | needs a UUID collision across clients at the same moment; the portal retries; fix adds a guard | reject |
| 7 | Admin children ordered by UUID after the default, not creation order; handoff silent | low | Meta ordering `['client', '-is_default', 'id']`; fix is a handoff sentence | patch (docs) |

## Verification

**Commands:**
- core, admin and portal full suites -- expected: 0 failures each
- `makemigrations --check` in admin and portal -- expected: no changes

## Staging

- mytp-core v0.28.0: commit cf21dea and tag pushed by the user. CI passed on master and the tag (runs 44305692624, 44305692962).
- Admin PR mytp-admin#43 merged as c6132c9. PR CI `test` passed twice. Master run 44308039124 passed, including the admindev deploy. `/healthz` is ok. Migration `clients` 0003 applied.
- Portal PR #112 merged as 27085c5. PR CI `test` passed twice (runs 44305298190, 44305312566). Master run 44309119708 passed: test plus deploys to dev and dev-ae.
- `sync_clients --all`:
  - dev: 2 `client.updated`, 1 skipped (staff);
  - dev-ae: 2 `client.updated`.
- Checks on dev with real writes, using the test doctor 1c410768 and test rows named "Staging69", read on admindev after each step:
  1. Two fiscal entities and two addresses created. All four arrived on admindev with ciphertext in the encrypted columns.
  2. An edit, a default change and `mark_used` updated the admin rows: version 2, the default moved, `used_at` set.
  3. Deleting the used rows archived them on both sides; deleting the unused ones removed them on both sides.
  4. Clean-up deleted the test rows on dev, and admindev followed to zero child rows.
- No new FailedEvent on admindev. The only one is from 2026-11-05 (`sample.recorded`, region mismatch) and is unrelated.
- Check 6 (the user's look at the new form error in EN and ES) has not been reported yet.
