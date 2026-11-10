---
title: 'Apply the current snapshot again after every portal deploy'
type: 'bugfix'
ticket: ''
created: '2026-11-10'
status: 'built'
baseline_revision: '863d92cd6aee1feaa1d983f3a1742c5a903d06e9'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A portal release that reads more of the configuration snapshot (new catalogue parts, a new section) only stores them when a snapshot is applied, and the portal answers `unchanged` for the version it already applied. Entries 5 and 6 of epic-catalogue worked around it with a migration that clears the applied version, which is missed when the previous release pulls between `migrate` and the switch, or when a failed deploy is retried (deferred-work.md, review finding 7 of entry 6): the portal then keeps a snapshot without the new parts until the next admin write.

**Approach:** The portal records which release applied the snapshot, next to the version. A pull whose version equals the applied one is `unchanged` only when this same release applied it; otherwise the snapshot is applied again in full. No migration clears the version any more.

## Boundaries & Constraints

**Always:**
- A lower version is still refused (`stale`); verification, the single transaction and the failure codes are unchanged.
- A release that does not know the new column (the one before this change, during a deploy or after a rollback) keeps working; what it applies is applied again by the next release that does.
- Nothing changes on the admin site or in mytp-core.

**Never:** no forced pull from a page request; no change to what a snapshot contains.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Same release, same version | version N applied by this release, pull of N | `unchanged`, nothing rewritten | — |
| New release, same version | N applied by another release, pull of N | applied again in full, recorded as applied by this release | an applier error rolls back and keeps the previous rows (`apply_failed`) |
| Previous release pulled in between | this release applied N, an older release applied N+1 (it does not write the mark), this release pulls N+1 | applied again | — |
| Pull during the deploy | the previous release applies N after `migrate` and before the switch | the new release applies N again at its first pull | — |
| Lower version | pull of N−1 | `stale` | — |
| No release identity | local development, tests (no REVISION file, no RELEASE_SHA) | behaves as one unnamed release: same version is `unchanged` | — |

## Decisions

- Decision (user, 2026-11-10): "another release" is the deployed git SHA (`REVISION` / `RELEASE_SHA`, already read for Sentry): every deploy applies the current snapshot once more at its first pull.

</frozen-after-approval>

## Code Map

- Repository: the portal (this checkout), on the branch `fix/reapply-snapshot-after-deploy`, already checked out: work there, commit nothing, push nothing. Run the full suite as the README says (the venv is `../.venv`). The line numbers below may be a little off; search by name.

- `integration/config.py`: `_apply` :156-176 (`snapshot.version <= row.version` → `UNCHANGED` / `STALE`), `pull` :204-232 (always fetches the full snapshot, so only `_apply` decides), the module docstring item 4.
- `integration/models.py` `ConfigState` :149-177 (one row; `version`, `applied_at`); a migration adds the mark (a nullable or defaulted column, so the previous release still saves the row with `update_fields=['version', 'applied_at']`).
- `mytreatplan/settings.py:235-238`: `SENTRY_RELEASE` from `RELEASE_SHA` or the `REVISION` file `deploy/release.sh:172` writes.
- `integration/management/commands/show_config.py`: prints the applied version; add the release that applied it.
- `catalogue/migrations/0002_products_core.py`, `0003_add_ons_quantities_calls_costs.py`: they clear `ConfigState.version`; applied migrations are left as they are, their docstrings and the `deploy/README.md` passages ("The deploy applies the current snapshot again, once…", the two exceptions, the remedy) are rewritten to say the mechanism is now this one.
- Tests: `integration/tests/test_config.py` (apply, unchanged, stale), `catalogue/tests/test_add_ons_costs.py` and `test_catalogue.py` (the two migration tests).

## Tasks & Acceptance

**Execution:**
- [x] `integration/models.py`, a migration -- the mark of the release and version that applied the snapshot.
- [x] `integration/config.py` -- `_apply` compares version and mark; docstring.
- [x] `integration/management/commands/show_config.py` -- show it.
- [x] `deploy/README.md`, the two catalogue migration docstrings -- the new mechanism; remove the exceptions and the manual remedy.
- [x] Tests for every row of the matrix.

**Acceptance Criteria:**
- Given version N applied by the previous release, when this release pulls N, then every section is applied again and a second pull is `unchanged`.

## Implementation Notes

- `ConfigState.applied_release` (the release) and `applied_release_version` (the version it applied): the mark carries the version because a release from before the mark moves `version` and leaves the mark behind. `_apply` answers `unchanged` only when the mark is this release's and of the applied version.
- The release identity (`integration.config.current_release`) is the release's own `REVISION` file, read at each pull; `SENTRY_RELEASE` (`RELEASE_SHA`) only without one, since a `RELEASE_SHA` in a site's shared settings would be the same for every deploy (review finding 2). Without either the site is one unnamed release.
- The first deploy of this change applies the current snapshot once on every site (the existing row has no mark). `show_config` prints `applied by release:`.
- Catalogue migrations 0002 and 0003 still clear the version (applied migrations are not edited); their docstrings, `catalogue/applier.py` and `deploy/README.md` now describe this mechanism. This closes the deferred-work entry of entry 6's review finding 7.
- Suite on SQLite: 2487 passed (+ the 3 known PostgreSQL-only failures); the concurrency test runs on PostgreSQL only (CI).

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, 2026-11-10): 5 findings — 5 low, 0 false. No unmet acceptance criterion, no functional bug.

| # | Where | Finding | Verdict | Route | Evidence / action |
|---|-------|---------|---------|-------|-------------------|
| 1 | `pull_config.py` docstring | still says a snapshot is applied only when its version is higher | low | patch | rewritten |
| 2 | `current_release`, README | a `RELEASE_SHA` in the shared settings wins over `REVISION` and is the same for every deploy, turning the mechanism off | low | patch | nothing sets it today; the identity is now the `REVISION` file first, tested |
| 3 | tests | the re-apply was proven only with a probe applier | low | patch | a test with the real catalogue and provider costs appliers added |
| 4 | README | "every deploy applies again" is wider than the behaviour (same SHA; rollback to a marked release) | low | patch | reworded, both cases stated |
| 5 | `deferred-work.md` | the entry this change resolves is still listed | low | rejected | entries are append-only by the workflow's rule; the Implementation Notes say it is closed |

## Verification

**Commands:**
- The portal suite — all pass but the 3 known PostgreSQL-only tests; CI on PostgreSQL.

**Manual checks (staging):**
- After the deploy, `pull_config` on dev prints `applied` for the current version once, then `unchanged`; `show_config` shows the release.
