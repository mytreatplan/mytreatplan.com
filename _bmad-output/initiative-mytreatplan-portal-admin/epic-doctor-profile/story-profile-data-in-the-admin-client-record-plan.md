---
title: 'Profile data in the admin client record'
type: 'feature'
ticket: '9'
created: '2026-11-17'
status: 'ready-for-dev'
baseline_revision: ''
route: 'full'
route_source: 'pinned'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
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
  - The snapshot carries the active entities and addresses, plus the archived ones with their `archived_at`, at most the 100 most recently archived per list.
  - The admin stores them all, and its `ClientRecord` exposes `active_*` and `archived_*` querysets.
  - The epic-sales-finance handoff asks its page to show active ones by default, with a "Show archived" filter for auditing.
  - (Claude's reading of "a, but with an option to recover the archived ones": the data must already be on the admin side for a future filter. Orders keep their own snapshots, so they do not depend on this.)
- **Fields:**
  - Fiscal: id, kind, first and last name, legal name, tax id, VAT id, the address lines, city, postal code, province, country, billing email, `is_default`, `used_at`, `archived_at`, `version`, `vies_status`, `vies_checked_at`.
  - Shipping: id, label, recipient name, clinic name, contact phone, delivery notes, the address fields, country, `is_default`, `used_at`, `archived_at`, `version`.
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
- No key starts with `patient` (`contracts/checks.py`). A full snapshot (10 + 10 active, 100 + 100 archived) stays below `MAX_EVENT_BYTES`; a test proves it.
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
| 5 | 101 archived addresses | Only the 100 most recently archived are sent; the oldest admin row is deleted |
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

## Tasks & Acceptance

1. **Core 0.28.0:** the item models, the optional lists on writer and reader, the schema, tests (parity; no maker or photo keys; the size of the largest snapshot), README and changelog.
2. **Admin:** the child models and migration, the sync in `_apply_client_updated`, the erasure cascade test, `active_*` and `archived_*` querysets, and tests for matrix rows 1-11 and 13 with crafted events. Vendor 0.28.0.
3. **Portal:** `payload_of` lists (active, plus the last 100 archived each); emits at every write point listed, including the VIES job with the user lock; test updates; and tests for rows 1-7, 12, 15 and 16. Vendor 0.28.0.
4. **Docs:**
   - handoff in the epic-sales-finance Notes (the page shows these lists, active by default with a "Show archived" filter, the tax id masked per role);
   - deferred-work entry for admin tax-id search;
   - README sections in the portal and the admin.
5. **Verification:** core, admin and portal suites with 0 failures; `makemigrations --check` clean in admin and portal.

**Acceptance:** the ticket's verify, plus matrix rows 1-16 covered by tests.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Verification

**Commands:**
- core, admin and portal full suites -- expected: 0 failures each
- `makemigrations --check` in admin and portal -- expected: no changes
