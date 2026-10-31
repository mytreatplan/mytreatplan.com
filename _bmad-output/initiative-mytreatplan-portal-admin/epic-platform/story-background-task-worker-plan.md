---
title: 'Background task worker'
type: 'feature'
ticket: '9'
created: '2026-10-30'
status: 'built'
baseline_revision: '322fd92c197d36c587c550f831286b3713fb75f5'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
  - '{project-root}/deploy/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Part 2 needs on-demand background work (outbox delivery, emails and SMS, webhooks, virus scans, the 24 h assignment confirmation, renewals), and AD-8 settles it as Django's `django.tasks` API with a PostgreSQL-backed worker per site, idempotent tasks, and no Redis or Celery. Nothing runs tasks today; only scheduled jobs exist.

**Approach:** Install `django-tasks-db` 0.13.0 as the `django.tasks` backend after proving it on Django 6.1 with the project's own tests; run one worker systemd service per site through the site-aware tooling (provision installs it, the release command restarts it after a release); add to mytp-core the `enqueue_on_commit` helper and the idempotency convention (a task keyed by record UUID, and by period for periodic effects, runs its effect once); monitor the worker with a heartbeat: a scheduled job enqueues a task that checks in to Sentry monitor `mytp-<SITE>-worker`; prune old task results on a schedule.

## Decisions

- Server step (user, 2026-10-30): after the merge Claude copies deploy/ to the staging host and re-runs provision for dev over SSH.

## Boundaries & Constraints

**Always:** task code imports only `django.tasks` and the mytp-core helpers, never the backend (AD-8); retries that matter live on business rows, not in backend options; the worker runs as the site's own Linux user with the site's settings.env; a release never leaves an old worker running new-schema tasks (restart after switch); the admin site's worker is entry 10's.

**Never:** no Redis, Celery or other broker; no business tasks yet beyond a sample and the heartbeat; no change to scheduled jobs except the new heartbeat and prune lines.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Enqueue after commit | A view saves a record and enqueues a sample task in the same transaction | Task runs once after commit; nothing runs if the transaction rolls back | — |
| Rerun | The same task for the same record UUID runs twice (worker killed after the effect) | Effect happens once | — |
| Periodic | Same record and period enqueued twice | Effect once per period | — |
| Worker dies | systemd kills the worker mid-task | systemd restarts it; the task is picked up again; idempotency holds | — |
| Release | New release switched | Worker restarted on the new code | Release fails and rolls back if the worker cannot start |
| Heartbeat | Worker down for longer than the heartbeat interval | Sentry monitor `mytp-dev-worker` misses a check-in and alerts | — |

</frozen-after-approval>

## Code Map

- `requirements.txt` -- add `django-tasks-db==0.13.0` (declares Django 5.2/6.0; 6.1 proven by this story's tests and CI on PostgreSQL 17).
- `mytreatplan/settings.py` -- `TASKS` setting with the database backend; `INSTALLED_APPS` gains its app.
- `../mytp-core/mytp_core/tasks.py` (new) -- `enqueue_on_commit(task, *args, **kwargs)` (wraps `transaction.on_commit`) and the idempotency helper (a small table or unique key recording effects done per key/period); tag `v0.3.0`, vendor it.
- `deploy/systemd/worker.service.template` (new) -- `mytp-<site>-worker.service`, user `mytp-<site>`, `WorkingDirectory=/srv/mytp-<site>/current`, `ExecStart=… manage.py db_worker`, `Restart=always`; installed and enabled by `deploy/provision.sh` (systemd units section :189-214).
- `deploy/sudoers/deploy.template` -- allow the site user to restart its worker too.
- `deploy/release.sh` -- restart `mytp-<site>-worker` after the switch (beside the gunicorn restart, :150-151); include it in the health/rollback flow.
- `deploy/jobs.conf` -- `workerheartbeat` (every 5 minutes: enqueues the heartbeat task) and `prunetasks` (daily: `prune_db_task_results`).
- Tests: `mytp_publicsite/tests/test_deploy.py` patterns for templates and provision; new tests for the helper, idempotency, heartbeat task and worker unit.
- **Note:** `release.sh` changes reach the host only when provision runs again (1.1's README note, deferred-work CI guard).

## Tasks & Acceptance

**Execution:**
- [x] `../mytp-core` -- `tasks.py` helpers with tests; tag `v0.3.0` locally (no push); vendor the wheel.
- [x] `requirements.txt`, `mytreatplan/settings.py` -- backend installed and configured.
- [x] `deploy/systemd/worker.service.template`, `deploy/provision.sh`, `deploy/sudoers/deploy.template`, `deploy/release.sh` -- worker unit per site, restart on release.
- [x] `deploy/jobs.conf` and a heartbeat task -- worker heartbeat monitor and result pruning.
- [x] tests for every I/O row that can run in CI; `deploy/README.md` -- the worker, its logs and the heartbeat monitor.

**Acceptance Criteria:**
- Given the merge deployed and provision re-run on staging, when a sample task is enqueued on dev, then dev's worker runs it once and `mytp-dev-worker` receives heartbeats.
- Given `systemctl kill mytp-dev-worker`, when systemd restarts it, then pending tasks run and none runs its effect twice.

## Implementation Notes

- `../mytp-core` is the local clone at v0.2.1 (pushed). Commit and tag `v0.3.0` there; do not push either repo.

## Plan Change Log

- 2026-10-30 (implementation): django-tasks-db marks a claimed task RUNNING and never picks a RUNNING task up again, so a SIGKILLed worker would strand it (the "Worker dies" row). Added mytp-core command `requeue_interrupted_tasks` (RUNNING -> READY for the backend), run as the worker unit's `ExecStartPre`; safe because each site runs exactly one worker and systemd starts the new one only after the old one stopped. It imports the backend lazily (extra `mytp-core[tasks]`); task code still never does. Also makes `systemctl restart` fail (and the release roll back) when settings or the database are broken.
- 2026-10-30 (implementation): "every 5 minutes" is not in the jobs.conf schedule subset; mytp-core 0.3.0 `oncalendar_to_crontab` accepts a minute step (`*-*-* *:00/5:00 UTC` -> `*/5 * * * *`).
- 2026-10-30 (implementation): the heartbeat task (`mytp_core.tasks.worker_heartbeat`) and its enqueue command (`enqueue_worker_heartbeat`) live in mytp-core so the admin site (entry 10) reuses them; the monitor schedule comes from the jobs.conf line, margin 3 min; a heartbeat queued over 5 min is skipped (a recovering worker's backlog must not report it alive).
- 2026-10-30 (implementation): idempotency helper is `run_once(effect, record_uuid, fn, period='')` over table `mytp_core_taskeffect` (unique effect+record+period), written in the same transaction as the effect. External effects are at least once (documented). mytp_core's AppConfig fixes `default_auto_field` so its migration doesn't depend on the site.
- 2026-10-30 (implementation): sample task `mytp_publicsite.tasks.sample_task` (logs one line per record via run_once) and command `enqueue_sample_task [--record]` for the staging check.
- 2026-10-30 (implementation): `release.sh` skips the worker step when `/etc/systemd/system/mytp-<site>-worker.service` is absent (the release command is host-wide; admindev has no worker until entry 10). Worker check: restart succeeds and the unit is still active 3 s later. `provision.sh` starts the worker only when the live release's requirements.txt has django-tasks-db.
- 2026-10-30 (implementation): `prunetasks` keeps successful results 14 days and failed ones 30 (`--queue-name *`), daily 04:30 UTC.
- 2026-10-30 (implementation): new pin `django-stubs-ext==6.1.2` (django-tasks-db's dependency). `conftest.py` tells SQLite's features it supports JSON fields (django-tasks-db's JSONFields made system checks open a DB connection in tests without DB access; PostgreSQL unaffected).

- 2026-10-30 (review fixes): `release.sh` stops the worker before `migrate` (new sudoers `stop` rule), starts it after the switch, restarts it on the unchanged release if a pre-switch step fails, and every rollback restarts both services; `has_worker` = unit enabled. `provision.sh` installs/enables/starts the unit only when the live release has django-tasks-db, and warns (continues) if it cannot start. Unit: `StartLimitIntervalSec=300`, `StartLimitBurst=10`, `--queue-name '*'`. LOGGING gains a `django.tasks` console logger (`propagate: False`) so task states and failures reach the journal with DEBUG off. mytp-core `enqueue_on_commit`'s alias parameter renamed `on_commit_using` (keyword-only); `v0.3.0` commit amended and tag moved, wheel re-vendored.

## Review Triage Log

- Pass 1 (quick): 6 findings; high 1, medium 3, low 2. All patched.
  - high, patch: the old worker kept running during migrate, the switch and the health loop (and stayed if gunicorn was unhealthy), against the frozen rule. release.sh now stops it before migrate, starts it after the switch, and restarts both on every rollback; sudoers allows stop.
  - medium, patch: provision installed and enabled the worker even when the live release could not run it, with unlimited restarts. It now installs only when the release has django-tasks-db, with StartLimitIntervalSec=300 / StartLimitBurst=10; has_worker tests that the unit is enabled.
  - medium, patch: a worker that failed to start aborted provisioning before jobs, Nginx and certificate. Now a WARNING and continue.
  - medium, patch: with DEBUG off the task state lines and failure tracebacks never reached the journal (django.tasks propagated to a debug-only handler). A django.tasks logger with its own console handler.
  - low, patch: enqueue_on_commit's `using` swallowed a task argument of that name; renamed to keyword-only `on_commit_using`.
  - low, patch: the worker processed only the default queue; it now runs `--queue-name '*'`.
- After patches: mytp-core v0.3.0 moved to 99850e3 (unpushed before), clean clone passes; wheel rebuilt; portal full suite 1529 passed, 3 known PostgreSQL-only failures; check and makemigrations --check clean; bash -n clean. Earlier full PostgreSQL 17 run: 1531 passed, and a real db_worker on Django 6.1 ran the sample task once and the heartbeat.

## Verification

**Commands:**
- Portal full suite, from the repo root (non-secret test keys; venv `../.venv`; browser tests need the sandbox off): `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/x19.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: all pass except the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py`.
- mytp-core tests from a clean clone of `v0.3.0`; `manage.py check`; `bash -n deploy/*.sh`.
- If a local PostgreSQL is available: run `db_worker` against it with a sample task to confirm django-tasks-db on Django 6.1.

**Manual checks (if no CLI):**
- After the merge: copy `deploy/` to the server and re-run `provision.sh dev dev.mytpdso.com mytp-team` (installs the worker unit, sudoers rule and the new release command), re-run or wait for the deploy, then enqueue a sample task on dev and watch `journalctl -u mytp-dev-worker`.

## Staging

- Merged in PR #54 as merge commit d7fc69c9a08be93ef26b58a698704839cc68ef69 (2026-10-30). CI run 41115480956 on master: test and deploy-staging green. mytp-core v0.3.0 CI green.
- Server step by Claude over SSH with the user's OK (2026-10-31), after the deploy: copied deploy/ and re-ran provision for dev (13 changes: mytp-release, sudoers, mytp-dev-worker installed, enabled and started, workerheartbeat and prunetasks timers).
- Checks: mytp-dev-worker active; enqueue_sample_task ran once (state SUCCESSFUL in the journal) and a second enqueue for the same record was skipped ('already done'); after `systemctl kill` systemd restarted it (NRestarts 1) and it is active; mytp-dev-job@workerheartbeat result success.
- Pending (user): Notify on the Sentry monitors mytp-dev-worker, mytp-dev-workerheartbeat and mytp-dev-prunetasks; then this plan is marked done.
