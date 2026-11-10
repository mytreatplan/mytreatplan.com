---
title: 'Interface operations'
type: 'feature'
ticket: '10'
created: '2026-11-09'
status: 'done'
baseline_revision: 'portal 241308a, admin 642fa37'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** I2, I4, I5: the interface works but is not yet operable for production: `/api/` is open to any IP (nginx comment "Production adds an IP allowlist here"), the signing-key rotation is a short note rather than a tested runbook, and a stuck outbox is noticed only after 24 h (and only when a send is attempted) while a stopped config pull is not noticed at all (cron monitors are off on the current Sentry plan).

**Approach:** `provision.sh` gains an optional peer-IP allowlist for `/api/` (rendered only when given); a step-by-step rotation and revocation runbook for both sides (verified on admindev); a small `checkinterface` job on each portal that sends one Sentry warning per outage when the oldest undelivered outbox event is older than 15 minutes or the last successful config pull is older than 1 hour. No mytp-core change.

## Boundaries & Constraints

**Always:**
- Allowlist: inside the `/api/` location only (the public site unaffected); `allow` lines for each given IPv4/IPv6 address or CIDR, then `deny all;`; values validated before any change; without the option no allowlist is rendered (staging) and provision says so. Each portal must allow the admin site's egress IP; the admin site must allow the portals'.
- Rotation runbook: generate the new key (only the public part printed) → add it to the peers' keyrings → restart (gunicorn and worker) → switch the signer → verify with `ping_peer` both ways and the `InterfaceCall` kid → remove/retire the old key; plus a compromise path (retire at once, failed calls accepted). Uses the existing `add_peer_key` / `retire_peer_key` on the admin and `INTERFACE_PEER_KEYS` on portals.
- Alerts: thresholds as settings (`OUTBOX_ALERT_MINUTES` = 15, `CONFIG_ALERT_MINUTES` = 60, validated by a system check); one Sentry warning per outage per kind, reset when healthy; counts and ages only (no payloads, ids or URLs); the check never sends or changes events.

- Decision (user, 2026-11-09): peer IPs come from an env var `API_PEER_IPS` given on each provision run, like `EXTRA_DOMAINS` (a run without it removes the allowlist, with a warning).
- Decision (user, 2026-11-09): rotation is a manual runbook in both READMEs, rehearsed on admindev during staging.
- Decision (user, 2026-11-09): the alert check is a new `checkinterface` job every 5 minutes on each portal.
- Decision (user, 2026-11-09): staging test with low thresholds set temporarily on dev (e.g. 2 and 5 minutes), admindev stopped briefly, then set back.
- Decision (user, 2026-11-09): one plan, a portal PR (deploy, job, settings, runbook) and an admin PR (runbook only), kept whole.

**Never:** no change to the outbox's retry or ordering, the config pull, the token rules, or the existing 24 h per-event warning; no allowlist on staging unless asked; no admin-side alert (the admin only ingests).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Allowlist set | provision with peer IPs | `/api/` allows them, denies others; `/` unaffected | — |
| Allowlist unset | provision without | no allow/deny lines; a notice | — |
| Bad IP | `API_PEER_IPS=999.1.1.1` | refused before any change | — |
| Stuck outbox | admindev stopped 20 min, an event recorded | one warning on dev | — |
| Still stuck | next runs | no second warning | — |
| Recovered | admindev back, delivered | state reset; a later outage warns again | — |
| Stale config | no successful pull for > 60 min | one warning | — |
| Rotation | runbook on admindev | no failed call during the switch | — |

</frozen-after-approval>

## Code Map

- Portal: `deploy/nginx/site.conf.template` `/api/` :72-85 (comment :72-74); `deploy/provision.sh` options header :28-36, `WSGI_MODULE` :54-56, `EXTRA_DOMAINS` :57-70, `render_nginx` :564-579 (marker-block pattern); tests `mytp_publicsite/tests/test_deploy.py` :243, render harness :570-640, EXTRA_DOMAINS tests :645-776; `integration/outbox.py` `WARN_AFTER` :62, `_warn_once` :267-273, `pending_due` :305, `dispatch` :310; `integration/config.py` `WARN_AFTER` :54, `_send_outage_warning` :179, `_record_failure` :187-197; `integration/models.py` `ConfigState` :146-167; `deploy/jobs.conf`; README "Rotate the signing key" :2533-2543, kid check :2510, dispatcher monitor :2566-2574, :2452.
- mytp-core: `interface.py` keyring :31-38; `generate_interface_signing_key` (no change).
- Admin: `interface/models.py` `PeerKey` :14-40; `add_peer_key`, `retire_peer_key` commands; `interface/calls.py` :55-58; settings :77-86; README :825-829.

## Tasks & Acceptance

**Execution:**
- [x] Portal: allowlist option + render tests; `checkinterface` job (settings, check, state for once-per-outage), `jobs.conf`, tests; runbook (allowlist, rotation, alerts).
- [x] Admin: rotation runbook (admindev side), allowlist note.

**Acceptance Criteria:**
- Given admindev is down for longer than the outbox threshold, when the check runs repeatedly, then exactly one warning is sent until delivery resumes.
- Given provision runs without the option, then the rendered `/api/` has no `deny all`.

## Implementation Notes

**Allowlist (I2).** `deploy/provision.sh`: `API_PEER_IPS` is validated right after `EXTRA_DOMAINS`, before the root check and before any `sudo` (exit 2, `Invalid API_PEER_IPS`). Pure bash, no python on the host needed: IPv4 dotted quad without leading zeros, IPv6 hex groups with at most one `::` (no zone id, no embedded IPv4), optional prefix 8-32 / 16-128, one line, lowercase-normalised, no duplicates. `deploy/nginx/site.conf.template`: inside `location /api/` only, a marker section `# __API_ALLOW_BEGIN__` / `__API_ALLOW__` / `deny all;` / `# __API_ALLOW_END__` (markers at column 0, like `__EXTRA_BEGIN__`). `render_nginx`: set → awk replaces the placeholder line with one `allow <value>;` per value (same indent), markers kept in the installed file; unset → the section is deleted with sed. The Nginx step prints `/api/ has no IP allowlist (API_PEER_IPS not set; production sets it).` on every run without it, and a WARNING (stderr) when the installed file has `# __API_ALLOW_BEGIN__` and this run drops it (the `EXTRA_DOMAINS` pattern). Tests in `mytp_publicsite/tests/test_deploy.py` (harness gains `peers=`): rules order and `deny all` inside `/api/` only (`/`, `/healthz`, `/static/`, ACME unaffected), no allowlist when unset (both renders), normalisation, 42 refused values through the real script under `no_privileges`, 13 accepted values, the notice/WARNING, and re-render removal. The existing "renders as before" test now strips the API section from its expectation as it does the EXTRA one.

**Alerts (I4, I5).** `integration/alerts.py` + `manage.py check_interface` + job `checkinterface | check_interface | *-*-* *:02/5:00 UTC` (every 5 minutes, offset 2 minutes from `pullconfig`/`workerheartbeat` at :00/5 so a pull in flight is not read mid-run). Kinds: `outbox` = the oldest `pending` OutboxEvent's `created_at` older than `OUTBOX_ALERT_MINUTES` (rejected and delivered rows ignored); `config` = `ConfigState.last_success_at` (before the first good pull: the row's `created_at`, the pull's own rule) older than `CONFIG_ALERT_MINUTES`; no ConfigState row (no pull ever tried) starts its own clock in `InterfaceAlert.since` (`not_checked` until the threshold). State: new model `InterfaceAlert` (pk `kind`, `warned_at`; migration `integration/0008_interfacealert`), locked per kind; `warned_at` saved first, the Sentry warning sent `on_commit`; a healthy run clears it (`interface alert cleared: <kind>` info line) and writes no row when none exists. Sentry: `capture_message(level='warning')`, tag `interface.alert=<kind>`, fingerprint `['interface-alert', kind]` (one issue per kind), message with counts and ages only (`Interface outbox not delivering for over 15 minutes: 2 event(s) pending, the oldest for 20 minutes`; `Configuration snapshot not pulled for over 60 minutes: last good pull 61 minutes ago`) — no ids, payload, URL or error code. Settings `OUTBOX_ALERT_MINUTES` (15) / `CONFIG_ALERT_MINUTES` (60) via `config(..., cast=int)`; system checks `integration.E008` / `E009` (int, not bool, >= 1). The command prints one line per kind and exits 0. The check reads only: it never touches OutboxEvent rows or ConfigState (tested). Tests: `integration/tests/test_alerts.py` (23).

**Runbooks.** Portal `deploy/README.md`: new "IP allowlist on `/api/` (production)" (with a scratch render check, tried locally), the provision `API_PEER_IPS` paragraph next to `EXTRA_DOMAINS`, "Rotate the signing key" rewritten (a portal's key 1-5, admindev's key 1-5, the compromise path), new "Interface alerts", and pointers in the Outbox and Configuration snapshot tables and the services table. Admin `README.md`: "Rotate the signing keys" (admindev side, both directions, compromise path) replacing the old **Rotation** paragraph, "IP allowlist on `/api/` (production)", and the intro's allowlist pointer. The new key's wrapped line goes from `generate_interface_signing_key` into `shared/signing-key.new` (umask 077) and only `INTERFACE_PUBLIC_KEY=` is printed; the switch is a scripted line replacement as the site user, the old line kept in `signing-key.old` until the old kid is retired. Kids use `sig<YYYYmmddHHMM>` so a same-day rotation does not reuse one.

**Deviations / choices not spelled out in the plan:**
- Prefixes wider than `/8` (IPv4) or `/16` (IPv6) are refused, `/0` included (review patch 3), and so is a value with a line break (`\n`, `\r`: word splitting would otherwise accept two lines as two addresses).
- The unset case stays fail-open (no allowlist, notice + WARNING), as decided by the user; review patch 2 leaves it for the coordinator to raise.
- The `config` alert does not change the pull's own one-hour warning (frozen block). Review patch 5: when `ConfigState.warned_at` is already set (the pull warned for this outage), the check sends nothing, marks its own `warned_at` and reports `still_failing` (ConfigState read only). The reverse order (the check warns first, then the pull in the same outage) still gives two; documented in the README.
- A site that never tried a pull (no ConfigState row): review patch 4 — the first such run records `InterfaceAlert(kind='config').since` (`not_checked`); once `since` is older than `CONFIG_ALERT_MINUTES` it warns once (`no pull tried yet, first seen <m> minutes ago`). A good pull later clears `since` and `warned_at` (`recovered`). ConfigState is never written by the check. The field is folded into migration 0008 (never committed).
- A non-integer threshold in settings.env fails at settings import (decouple `cast=int`), like `HUB_LOOKUP_TIMEOUT`'s float; the system check covers 0, negatives and non-int values set otherwise.
- The README staging step stops admindev with its socket too (`mytp-admindev-gunicorn.socket`), as stopping only the service lets socket activation start it again on the next call.

**Review patches (coordinator, 2026-11-09):** 1 and 8, both runbooks: the exact `INTERFACE_PEER_KEYS=` line for each state (old, old+new, new; only the new one on the compromise path, empty line when nothing is left), "remove the entry and its comma", `manage.py check` before every restart after a `settings.env` edit, and the verify queries filtered on `received_at__gte='<rotation start>'` (noted with `date -u` before step 1). 3: prefix widths as above. 4 and 5: as above. 6: `check_interface` ends with `sentry_sdk.flush(timeout=5)`. 7: tests for `_locked_alert`'s IntegrityError branch (the lookup is now `_find_locked`, patched to miss once), and refused values `1.2.3.4{`, `}`, `{ allow all; }`, `1.2.3.4}`, embedded `\n` / `\r`, `/1`, `0.0.0.0/1`, `::/1`, `10.0.0.0/7`, `2000::/15`; accepted `2001::/16`. New alert tests: never-pulled warn-once and reset, no second warning when the pull already warned, the flush.

## Verification results

- Portal suite, SQLite (sandbox off): 2418 passed, 8 skipped, 3 failed — the 3 known PostgreSQL-only failures in `accounts/tests/test_crypto.py`.
- Portal suite, throwaway PostgreSQL 17 cluster under `$TMPDIR` (Homebrew `pg_ctl`, stopped and deleted afterwards): 2429 passed, 0 failed, 0 skipped.
- Admin suite, SQLite: 1136 passed, 10 skipped (README-only change).
- After the review patches (2026-11-10): portal suite on SQLite 2436 passed, 8 skipped, 3 failed (the same 3 known PostgreSQL-only `test_crypto.py` failures); on a fresh throwaway PostgreSQL 17 cluster 2447 passed (stopped and deleted afterwards); `manage.py check` no issues; `makemigrations --check --dry-run` No changes detected. The admin change is README-only (suite not re-run).
- `manage.py check`: no issues; with `OUTBOX_ALERT_MINUTES=0`: `integration.E008`. `makemigrations --check --dry-run`: No changes detected.
- README render check run locally on a scratch copy: `/api/` renders `allow 203.0.113.7;`, `allow 2001:db8::1;`, `deny all;`; `API_PEER_IPS=999.1.1.1` exits 2 with `Invalid API_PEER_IPS`.

**Staging steps (in order, run by the user):**
1. Merge the portal PR (deploy, job, settings, migration 0008, runbook) and the admin PR (runbook only); CI deploys dev and dev-ae (migration `integration.0008`); admindev's release is unchanged.
2. On the host, fresh copies of the merged `deploy/` (`~/deploy`, and `~/deploy-admindev` with the admin's `jobs.conf`), then without `API_PEER_IPS`: `bash ~/deploy/provision.sh dev dev.mytpdso.com mytp-team < /dev/null`, the same for dev-ae, and `WSGI_MODULE=mytp_admin.wsgi bash ~/deploy-admindev/provision.sh admindev admindev.mytpdso.com mytp-team < /dev/null`. Expect the notice `/api/ has no IP allowlist` and no WARNING on each; dev and dev-ae install the `checkinterface` timer; each Nginx site file changes once (template comments) and reloads. A second run: No changes.
3. `systemctl list-timers 'mytp-dev*-job@checkinterface*'`; on dev and dev-ae `manage.py check_interface` prints `outbox ok` and `config ok`; `manage.py check` no issues. From a laptop, `curl -s -o /dev/null -w '%{http_code}' https://dev.mytpdso.com/api/v1/ping` is still 401 (no allowlist on staging). Disable the Sentry monitor `mytp-<site>-checkinterface` after its first run.
4. Render check with a sample `API_PEER_IPS` on a scratch copy (the README recipe), not applied to staging.
5. Rotation rehearsal: `ping_peer` both ways (dev ↔ admindev) before; rotate dev's key by "Rotate the signing key" (a portal's key, steps 1-5); then admindev's key (steps 1-5, dev and dev-ae accepting the new kid first); after each: `ping_peer` both ways, the call logs show the new kid, no `unknown_key` refusals, `pull_config` unchanged/applied, `nudge_config` ok.
6. Alerts: on dev set `OUTBOX_ALERT_MINUTES=2` and `CONFIG_ALERT_MINUTES=5`; stop admindev (`sudo systemctl stop mytp-admindev-gunicorn.socket mytp-admindev-gunicorn.service`); record an event on dev; within ~10 minutes Sentry (dev) shows one `outbox` and one `config` warning, later runs print `still_failing` and send nothing; start admindev (`sudo systemctl start mytp-admindev-gunicorn.socket mytp-admindev-gunicorn.service`); once delivered and pulled, the next run prints `recovered` for both. Remove the two settings lines (jobs read them per run; no restart).

## Plan Change Log

## Review Triage Log

Opus review, security and operations focus (2026-11-09); validation, rendering inside `/api/` only, no proxy in front, alert logic and rotation order checked sound.
- patch (medium): rotation runbooks could leave a stray comma in `INTERFACE_PEER_KEYS` (the keyring refuses it: every call fails) and checked only after the restart → exact resulting lines, "remove the entry and its comma", `manage.py check` before every restart.
- patch (low): prefixes wider than /8 (IPv4) and /16 (IPv6) refused; line breaks refused; more refused-input tests.
- patch (low): a site that never pulled is now watched (`InterfaceAlert.since`); no second config warning when the pull already warned; `sentry_sdk.flush`; IntegrityError branch tested; verify queries filtered from the rotation start.
- decision pending (user): the allowlist fails open when a re-provision omits `API_PEER_IPS` (the user's decision: removed with a warning); proposed: refuse unless `API_PEER_IPS=none`.

## Verification

**Commands:**
- Portal and admin suites — all pass; CI on PostgreSQL.

**Manual checks (staging):**
- Render check with a sample `API_PEER_IPS` on a scratch copy (not applied to staging); rotate admindev's signing key by the runbook with `ping_peer` before and after; low thresholds on dev, admindev stopped, one warning each kind, recovery resets; thresholds back.

## Staging

2026-11-09/10, run by Claude with the user's delegation. Portal PR #85 and admin PR #27 merged after their checks passed; deploys green (migration 0008 applied). Portal suite 2436 passed / 8 skipped + the 3 known PostgreSQL-only failures, 2447 on PostgreSQL 17.
- Re-provisioned dev, dev-ae (checkinterface timer added) and admindev without `API_PEER_IPS`: each printed the "no IP allowlist" notice, no WARNING; a second run: No changes. `check_interface` on dev and dev-ae: outbox ok, config ok. `/api/v1/ping` unsigned from the host: 401. (The allowlist render check was run locally by the implementer on a scratch copy; not applied to staging.)
- Alerts with thresholds 2 and 5 minutes on dev and admindev stopped (socket and service), one made-up contact message recorded: outbox `warned` at 2 minutes, then `still_failing`; config `warned` at 6 minutes, then `still_failing` (no repeat warnings). admindev started: the event delivered, both `recovered`; the two threshold lines removed (back to 15 and 60, `ok`).
- Rotation of dev's key: generated `sig202611092329` (only the public key printed), switched dev's signer, checked, restarted; admindev's old kid `sig20261105` retired; pings both ways and dev's pull pass. Incident during the rehearsal: Claude's own script cut the public key's trailing `=` (a `cut -d=` in the operator's shell, not the runbook), so `add_peer_key` refused it and, the script not stopping, dev signed with an unknown kid for about 30 seconds (one refused call, nothing lost: no pending events, the pull kept its snapshot); the key was then added and everything passed. Lesson for operators: copy the whole public key, padding included (the runbook's commands do).
- Rotation of admindev's key: generated `sig202611092330`; dev and dev-ae accepted both kids (`check` before each restart), admindev switched its signer; pings admindev↔dev, admindev↔dev-ae and both pulls pass, dev's call log shows admindev's calls with the new kid and 0 `unknown_key`; then each portal kept only the new entry (`check`, restart), all pings and pulls pass; `signing-key.old` removed.
- Pending with the user: whether a re-provision without `API_PEER_IPS` must refuse to remove an installed allowlist (fail closed) instead of warning.
- Settled (user, 2026-11-10): it refuses (exit 2) unless `API_PEER_IPS=none` is given; built in `../plan-allowlist-fail-closed-and-restore-pg18.md`, which replaces this entry's "removed with a WARNING".
