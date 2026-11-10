---
title: '/api/ allowlist fails closed; restore needs pg_restore 18'
type: 'bugfix'
ticket: ''
created: '2026-11-10'
status: 'built'
baseline_revision: '87cbc304078f0f07e662a46cfd295eaac57f876b'
route: 'oneshot'
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

**Problem:** Two follow-ups of epic-admin-interface entry 10 and epic-platform entry 14. A `provision.sh` run without `API_PEER_IPS` on a site that has the `/api/` allowlist removed it with only a WARNING, so a forgotten variable opened `/api/` on production. `deploy/restore-db.sh` accepted `pg_restore` 17 although the dumps are made by `pg_dump` 18, which it cannot read.

**Approach:** Decision (user, 2026-11-10), replacing entry 10's "removed with a WARNING": such a run is refused with exit 2 before any change; `API_PEER_IPS=none` removes an installed allowlist on purpose, with a WARNING. Decision (user, 2026-11-10): `MIN_PG_MAJOR` in `restore-db.sh` is 18.

</frozen-after-approval>

## Implementation Notes

- Oneshot: two small changes to deploy scripts, their tests and the runbook wording.
- `deploy/provision.sh`: `none` alone (any case, spaces around) sets `API_PEER_NONE=1`; `none` next to an address is an invalid value. A guard block before the first change (`# --- /api/ allowlist guard`) stops with exit 2 when the variable is unset and the installed Nginx site has the allowlist markers. The Nginx step warns only on the `none` removal. A site with no allowlist (staging) and a site not provisioned yet behave as before.
- `deploy/restore-db.sh`: `MIN_PG_MAJOR=18`; the header comment and the error message say why.
- Tests: `mytp_publicsite/tests/test_deploy.py` (refusal, `none`, `none` with an address, a new site), `mytp_publicsite/tests/test_backups.py` (the fake `pg_restore` is 18.1; 17.6 is refused).
- `deploy/README.md` "IP allowlist on `/api/`" says the new behaviour. The admin site's README repeats the old sentence and gets the same wording in its own repository.

## Plan Change Log

## Review Triage Log

Quick lens, 2026-11-10: 1 medium, 5 low, 1 deferred to its own repository; all real ones patched.

- medium, patch: a site given the allowlist while still HTTP-only (no certificate, so no `/api/` block) had no marker on disk, so a later run without the variable was not refused and the certificate run installed the site without the allowlist. `render_nginx 0` now ends the file with `# __API_ALLOW_PENDING__` when `API_PEER_IPS` is given, and the guard looks for either marker; tested.
- low, patch: the Nginx step's removal WARNING said `API_PEER_IPS=none` without checking it; it is now inside the `API_PEER_NONE` branch.
- low, patch: the README paragraph re-wrapped.
- low, patch: the README's provisioning summary now says a run without the variable is refused and names `none`.
- low, patch: entry 10's plan (`epic-admin-interface/story-interface-operations-plan.md`) gets a line that its pending question is settled here.
- low, patch: the test now also asserts the guard sits before the script's first step.
- low, defer to mytp-admin: its README repeats the old sentence; a docs change in that repository follows (its checkout is in use by epic-catalogue entry 5).

## Verification

**Commands:**
- The portal suite — expected: all pass but the 3 known PostgreSQL-only tests; CI on PostgreSQL. Locally (a worktree without the compiled translations and CSS): the deploy, backup and scan-deploy tests pass (263); the full run's other failures are the Spanish-text and browser tests that need those built files, so CI is the full check.

**Manual checks (staging):**
- No staging site has an allowlist, so a normal re-provision of dev still prints `/api/ has no IP allowlist (API_PEER_IPS not set; ...)`. The refusal is checked on production's first allowlist run (epic-eu-golive).
