---
title: 'Refactor sweep (epic-admin-interface)'
type: 'refactor'
ticket: '12'
created: '2026-11-14'
status: 'built'
baseline_revision: '650ac9f7c59e7e785fe179589687cfd35d11491d'
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

**Problem:** epic-admin-interface's entries left private helpers imported across modules, copied test keys and helpers, a few duplicated lines and stale one-off text, and its deferred review findings (one of them a logger whose lines never reach the journal) are neither closed nor recorded with their state today.

**Approach:** Cleanup only, in the portal and the admin repo: give the shared helpers public names in one place, remove the copies, close the logger finding, and bring `deferred-work.md` up to date (closed, or recorded and left). Everything that changes the wire, the data or mytp-core is recorded and left.

## Boundaries & Constraints

**Always:**
- No wire, migration, template or behaviour change, except the journal lines the logger fix adds; error texts and command output stay byte-identical.
- The suites pass with the baseline counts, minus tests named in the Implementation Notes.
- `deferred-work.md` entries are append-only: "closed by …" or "recorded and left".

**Never:**
- No mytp-core change (no release, no re-vendoring).
- No change to the staging-only sample (`integration/sample.py`, `SampleRecord`, `seed_sample_record`, admin `remote_sample.py`), `Attempt.FILE`, the test-only hooks (`section_applier_names`, `snapshot_apply_running`, admin `section_names`), the per-module `interface_settings` / `FakeAdmin` / `FakePortal`, or the parallel `client_sync` / `contact_sync` modules.
- No fix outside epic-admin-interface's subject.

## Decisions

- Decision (user, 2026-11-14): both `integration.alerts` and `integration.remote` are added to the portal's `LOGGING`.
- Decision (user, 2026-11-14): the mytp-core leftovers (two unused names, the code copied between the sites) are one "recorded and left" entry; no core release.
- Decision (user, 2026-11-14): the admin's `resync_client` and `resync_contact_message` commands share one helper; output and error texts unchanged.
- Decision (user, 2026-11-14): the removed sample event's transition test and README note are deleted; at staging a count-only grep confirms no site's settings name the old setting.
- Decision (user, 2026-11-14): the "disable the `purgeinterfacecalls` Sentry monitors" one-off text is deleted from both READMEs.
- Decision (user, 2026-11-14): the contact-message region guard limit is one "recorded and left" entry.
- Decision (user, 2026-11-14): merge and staging for this entry are delegated.

</frozen-after-approval>

## Code Map

Portal (`integration/`):
- `mytreatplan/settings.py:686-726` -- `LOGGING` lacks `integration.alerts` (`alerts.py:167`, promised by `deploy/README.md:2632`) and `integration.remote` (`remote.py:126,151,259,331,349`).
- Private names used across modules: `config.py:70-71` (`outbox._error_code`, `snapshot_tables._snapshot_apply`), `hub.py:60` (`outbox._error_code`, `_unavailable`), `remote.py:113,244` (`views._not_the_admin_site`), `views.py:45,47` (`calls._unavailable`, `calls._outcome`). Target: `admin_site_url`, `error_code`, `unavailable` public in `peers.py`; `not_the_admin_site` and `outcome` public in `calls.py`. Tests monkeypatch `hub.admin_site_url` and `outbox.admin_site`: the patched names must keep working. Docstrings `config.py:14`, `hub.py:7` name the old homes.
- `views.py:101-108` -- `clients_resync` repeats `_resync_uid` (`:75-83`).
- Alert defaults 15/60 in `settings.py:365-366`, `alerts.py:42-43`, `checks.py:27-28`.
- Test key seeds / SigningKeys copied in `integration/tests/test_hub.py:28-32`, `test_outbox.py:31-32`, `test_remote.py:23-28`, `test_ping.py:27-32`, `accounts/tests/test_client_sync.py:30-33`, `mytp_publicsite/tests/test_contact_sync.py:29-32`; the originals are `integration/tests/snapshot_rows.py:33-36`.
- `integration/tests/test_outbox.py:682-687`, `deploy/README.md:2707-2708` -- the sample event's transition test and note (delete). `deploy/README.md:2371-2375` -- the purge-monitor one-off (delete).
- Left: the two `_in_threads` copies (`test_outbox.py:758`, `test_remote_write.py:440`) differ in what they return.

Admin (`../mytp-admin`, baseline `bd1ef042b770f534b716f1f2b8e348555366f5e8`):
- Cross-app test imports: `audit/tests/test_audit.py:1221` (from `interface.tests.test_remote`), `contact_messages/tests/test_contact_messages.py:17,147` (from `clients.tests.test_clients`): move what they import to the root `conftest.py`.
- Seeds / SigningKeys copied in `clients/tests/test_clients.py:25-28`, `interface/tests/test_{hub,events,remote,ping}.py`; the root `conftest.py:194-198` has them. `DEV_AE` seeds and kids differ per module: only identical values are shared.
- `interface/hub.py:46`, `views.py:60-61,97` use `calls._outcome`, `calls._safe`; `interface/tests/test_remote_write.py:98` imports `_write_error`: public names.
- `interface/events.py:98-100` -- `_client_record`, a one-line wrapper with one caller.
- `interface/management/commands/resync_client.py`, `resync_contact_message.py` -- about 90 % the same: one helper.
- `README.md:804, 837-838` -- the purge-monitor one-off (delete).

Planning (`deferred-work.md`):
- Close: L93-95 (the logger); L15-17 and L30-32 (`fresh_code` wording: core `policy.py:8-16,49-51`, admin commit fa47691).
- Already closed, nothing to add: L39-44, L57-58. L91 covers core's `interface.SITE_RE`.
- Record and leave: the core leftovers (`contracts/checks.py:47` `PATIENT_FIELDS`, `interface.py:254` `signing_key_configured`, the `calls.py` / `purge_interface_calls` / `client_ip` copies); the contact-message region guard limit (admin `interface/events.py:87-95,252-254`, `README.md:1051-1055`).

## Tasks & Acceptance

**Execution:**
- [ ] Portal `mytreatplan/settings.py` -- add `integration.alerts` and `integration.remote` to `LOGGING`, like their neighbours; a test proves an INFO line of each reaches the handler.
- [ ] Portal `integration/peers.py`, `calls.py`, `outbox.py`, `config.py`, `hub.py`, `remote.py`, `views.py`, `snapshot_tables.py` -- the helpers of the Code Map get public names in `peers.py` / `calls.py` (and `snapshot_apply` public where it lives); no module imports a `_name` from another module; names tests monkeypatch keep working; docstrings name the new homes.
- [ ] Portal `integration/views.py` -- `clients_resync` uses `_resync_uid`.
- [ ] Portal `integration/alerts.py`, `checks.py` -- the 15/60 defaults are read from one place.
- [ ] Portal test modules of the Code Map -- seeds and SigningKeys come from `integration/tests/snapshot_rows.py`; only identical values are shared.
- [ ] Portal `integration/tests/test_outbox.py`, `deploy/README.md` -- delete `test_the_sample_setting_and_its_check_are_gone`, the sample-event note and the purge-monitor one-off; check the hunk edges (no heading or label lost) and cross-references.
- [ ] Admin `interface/calls.py`, `hub.py`, `views.py`, `events.py`, `interface/tests/test_remote_write.py` -- public `outcome`, `safe`, `write_error`; inline `_client_record`.
- [ ] Admin `interface/` + the two `resync_*` commands -- one shared helper; stdout, stderr and exit codes byte-identical (the existing command tests pass untouched).
- [ ] Admin root `conftest.py` and the test modules of the Code Map -- what `audit` and `contact_messages` tests import from other apps' test modules moves to the root `conftest.py`; identical seeds come from it.
- [ ] Admin `README.md` -- delete the purge-monitor one-off; same hunk-edge check.
- [ ] Portal `_bmad-output/…/deferred-work.md` -- append: closed (the logger; `fresh_code` wording), recorded and left (core leftovers; region guard limit).

**Acceptance Criteria:**
- Given the portal suite, when it runs, then 0 failed and the count is 3026 passed, 12 skipped, minus the deleted test, plus the logger tests.
- Given the admin suite, when it runs, then 1582 passed, 12 skipped.
- Given both trees, when grepped, then no non-test module in `integration/` or `interface/` imports or calls a `_name` of another module, and no test module of `audit` or `contact_messages` imports from another app's test module.
- Given both diffs, when `git diff --stat` is read, then no migration, template, `vendor/` or `deploy/*.sh` file changed, and `makemigrations --check --dry-run` reports no changes.
- Given `deferred-work.md`, when read, then every epic-admin-interface finding is closed or recorded with its state on 2026-11-14.

## Implementation Notes

- Tests: deleted `integration/tests/test_outbox.py::test_the_sample_setting_and_its_check_are_gone`; added `integration/tests/test_alerts.py::test_the_info_lines_reach_the_journal[integration.alerts]` and `[integration.remote]`. Portal expected count: 3026 - 1 + 2 = 3027 passed, 12 skipped. Admin count unchanged (1582 passed, 12 skipped).
- Portal: `admin_site_url`, `error_code`, `unavailable` (and the `NETWORK` / `PEER_URL` codes) moved from `outbox.py` to `peers.py`; outbox, hub and config import them from there (`hub.admin_site_url` and `outbox.admin_site` stay patchable; `outbox.admin_site_url` still resolves). `not_the_admin_site` moved from `views.py` to `calls.py` (remote.py imports it at the top: no more lazy import from views); `calls.outcome` public; `calls._unavailable` stays private (no other module calls it now). `snapshot_tables.snapshot_apply` public; `test_snapshot_tables.py`'s marker check follows the new name. `OTHER_SEED` joined `snapshot_rows.py` (identical in five modules).
- Portal alert defaults: `checks.py` reads `alerts.DEFAULT_MINUTES` (lazy import inside the check, like `hub.OVERALL_CAP`); `settings.py`'s `config(..., default=15/60)` stays, as settings cannot import the app.
- Admin: `_client_record` had two callers (not one); both now call `_locked_record(ClientRecord, envelope)`. The shared resync helper is `interface/resync.py::resync`. Root `conftest.py` gained the event-ingest senders (`EVENTS`, `DEV_AE` with its own seed, `CLIENT`, `CLIENT_UID`, `add_site`, `sites`, `client_envelope`, `send`, `FakePortal` unchanged) and the remote-region fake (`RECORD`, `NAME`, `NOTE`, `ASCII_MARK`, `FakeResponse`, `read_error` (was test_remote's `_http_error`), `write_error`, `FakeRegion`); contact_messages uses the root `interface_settings` module-wide (same body as clients'). clients' own autouse `interface_settings` and `portal` fixture stay.
- Admin `README.md:804` ("step 3 has run again (the `purgeinterfacecalls` timer; ...)") is kept: it is the `/api/` Nginx prerequisite, not the monitor one-off; only item 4 (disable the Sentry monitor) was deleted.

## Plan Change Log

## Review Triage Log

Quick lens (Opus, one reviewer per repo), 2026-11-14: 2 findings, both low, both patched; none deferred.

| # | Severity | Finding | Verdict |
|---|----------|---------|---------|
| 1 | low | `deferred-work.md`: the new `fresh_code` entry said "closed" without saying by what | patched: "closed by mytp-core 0.8.0 and mytp-admin commit fa47691" |
| 2 | low | Admin root `conftest.py`: the `interface_settings` docstring listed two module-wide users; `contact_messages/tests/test_contact_messages.py` is now a third | patched: docstring names it |

## Design Notes

- Working notes for the implementer: the branch is `story/4.12-refactor-sweep`, already created in the portal checkout and in `../mytp-admin`; do not create another branch and do not commit. Do not touch `../mytp-core`. Admin suite: `SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver DATABASE_URL=sqlite:///$TMPDIR/adm.sqlite3 ALLOW_FAKE_VERIFICATION=True` plus the portal's two test keys, with `/private/tmp/claude-501/admin-venv2/bin/python -m pytest -q -p no:cacheprovider`.
- Baselines: portal 3026 passed, 12 skipped, 0 failed (8.2); admin 1582 passed, 12 skipped.

## Verification

**Commands:**
- the portal's full suite command (SQLite, `../.venv`, sandbox off) -- expected: 3026 passed, 12 skipped, minus tests named in the notes
- the admin suite (admin venv) -- expected: 1582 passed, 12 skipped
- `manage.py makemigrations --check --dry-run` in both repos -- expected: no changes
- `git diff --stat` in both repos -- expected: no migration, template, `vendor/` or core file
