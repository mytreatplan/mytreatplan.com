---
title: 'dev-ae staging site'
type: 'feature'
ticket: '7'
created: '2026-11-01'
status: 'built'
baseline_revision: '9e31bb25a1e56cdbc3f78b0bd6d5a4c1580001d2'
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

**Problem:** Only one portal site (dev, REGION=eu) runs on staging, so nothing proves the same build runs as a second region; the platform epic needs a REGION=ae site deployed from master beside dev.

**Approach:** Bring up dev-ae.mytpdso.com on the staging host through the existing site-aware tooling (provision, host-wide `mytp-release`, per-site user and home), with its own database, settings.env, its own field data keys wrapped by dev's EU KMS service key (staging only, per the 2026-10-29 decision), and make the portal's staging CI deploy every master build to both dev and dev-ae.

## Boundaries & Constraints

**Always:** dev keeps deploying exactly as now; a failed dev-ae deploy never blocks or rolls back dev, and vice versa. Both sites get the same commit. dev-ae's settings.env is built on the server without printing values; the user types the database password. New site databases follow the 3 setup steps (right OVH service host with URL-encoded password and `sslmode=require`; staging IP in that service's authorized IPs; database and `public` schema owned by the site user), checked with psql before the first deploy. Plan notes name no database hosts, ports, IPs or task ids.

**Never:** no UAE KMS, UAE storage or UAE production settings (epic-uae-launch). No per-site backup restore test or Sentry environments (entry 13). No code branches on region outside `regions/`. No change to the release, provision or unit templates unless a real defect shows up.

## Decisions

- CI: a matrix over `[dev, dev-ae]` with `fail-fast: false` and a per-site concurrency group; each leg builds its own tarball of the same sha and runs its own tip check (user, 2026-11-01).
- Deploy key: dev-ae gets its own key pair; private half in a new repo secret `STAGING_SSH_KEY_DEV_AE`, chosen per matrix leg (user, 2026-11-01).
- Database: a new database `mytp_dev_ae` with user `mytp_dev_ae` on dev's existing OVH PostgreSQL service (user, 2026-11-01).
- Backups: same bucket and S3 user as dev with `BACKUP_PREFIX=staging-dev-ae/` (user, 2026-11-01).
- Turnstile: add `dev-ae.mytpdso.com` to dev's existing widget and reuse its keys (user, 2026-11-01).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Both deploy | push to master, both sites provisioned | dev and dev-ae switch to the same sha; each /healthz 200 | No error expected |
| dev-ae fails | dev-ae release fails (bad settings) | dev still deploys; dev-ae keeps its current release; CI red on the dev-ae leg | release.sh rollback, job fails |
| Not tip | a newer master exists | neither site uploads or releases | tip check skips both |

</frozen-after-approval>

## Code Map

- `.github/workflows/ci.yml` :86-172 `deploy-staging` -- one site via `env.SITE: dev`; SSH config `User mytp-$SITE` from secrets STAGING_HOST/STAGING_SSH_KEY/STAGING_KNOWN_HOSTS; tip check id `tip`; steps 'Upload release' and 'Release' run `scp …/srv/mytp-$SITE/incoming/` and `ssh staging /usr/local/bin/mytp-release "$SITE" …`; concurrency `deploy-staging`; environment url dev.mytpdso.com.
- `mytp_publicsite/tests/test_deploy.py` -- pins the job: fixture :42, concurrency :58-63, `env.SITE == 'dev'` and exact strings :173-181, step names and `if` :184-193, staging.env.example dev values :225-237. Per-site rendering tests already use `dev-ae` (:299) -- keep.
- `deploy/staging.env.example` -- per-site values: SITE :7, REGION :11, SECRET_KEY :12, DATABASE_URL :13, ALLOWED_HOSTS/CSRF/SITE_URL :16-18, TURNSTILE :37, DB_SSLROOTCERT :72, BACKUP_PREFIX :89, KMS cert/key paths :107-119, FIELD_KMS_KEYS/BLIND_INDEX_KMS_KEY :138-140. Keep it dev's example; add a short "dev-ae differences" comment block.
- `deploy/README.md` -- "Sites on the staging host" :1302 and "Add a site" :1326 (expand into a dev-ae runbook); steps 5-6 :100-137 (deploy key and secrets, dev only).
- Field keys: `../mytp-core` commands `generate_kms_field_key <key_id>` and `--blind-index`, then `kmscheck`, run as `mytp-dev-ae`. FIELD_ENCRYPTION_KEYS and BLIND_INDEX_KEY stay empty (new environment starts on KMS).
- No app code changes expected: settings derive everything from SITE/REGION (settings.py:46-75); `regions/tests/test_settings.py::test_ae` loads REGION=ae; healthz prints site and region.
- Do not change: `deploy/provision.sh`, `deploy/release.sh`, templates, `regions/profiles.py`, mytp-core.

## Tasks & Acceptance

**Execution:**
- [x] `.github/workflows/ci.yml` -- matrix deploy to dev and dev-ae (see Decisions), same sha, per-site SSH key secret, per-site concurrency group and environment url, tip check per leg.
- [x] `mytp_publicsite/tests/test_deploy.py` -- update the pinned job shape: both sites deployed, a dev-ae failure cannot stop dev, pinned host keys still from secrets, release only when tip.
- [x] `deploy/staging.env.example`, `deploy/README.md` -- dev-ae differences and an "Add dev-ae" runbook: DNS (done), database with the 3 setup steps, provision, settings.env, KMS cert copy and own data keys, deploy key and secret, Turnstile hostname, first deploy, checks.
- [ ] server (hitl) -- user creates `mytp_dev_ae` on dev's service (3 setup steps); provision `dev-ae dev-ae.mytpdso.com mytp-team`; settings.env; KMS files and keys; deploy key; first deploy; checks.

**Acceptance Criteria:**
- Given both sites provisioned, when master is pushed, then dev and dev-ae both run the pushed sha and `/healthz` shows `site: dev-ae` and `region: ae` on dev-ae, `site: dev` and `region: eu` on dev.
- Given dev-ae running, when a user signs up on dev-ae, then the user exists in dev-ae's database and not in dev's (counts only).
- Given dev-ae's keys, when `kmscheck` runs as mytp-dev-ae, then it succeeds.

## Implementation Notes

- CI: `deploy-staging` is a matrix (`include` of site, domain, ssh_key_secret) with `fail-fast: false`, concurrency `deploy-staging-${{ matrix.site }}`, environment url `https://${{ matrix.domain }}`, `SITE: ${{ matrix.site }}`, `SHA: ${{ github.sha }}`. The key is `secrets[matrix.ssh_key_secret]`; "Configure SSH" fails the leg with a clear error when that secret is empty. Steps are otherwise unchanged (each leg builds, checks tip and releases on its own).
- Tests: the concurrency/env/pinned-key/SITE tests now pin the matrix shape; new tests: exact matrix, fail-fast off and no step or job depending on the other site, missing-key guard before the key is written.
- Docs: staging.env.example keeps dev's values plus a "dev-ae differences" comment block. README: intro, step 6 note, "How a deploy works" concurrency, deploy-key rotation, and a new "Add dev-ae" runbook under "Sites on the staging host" (DNS, the 3 database steps with a psql owner/create-table check, provision, CA and KMS cert copies, settings.env built from dev's with grep -v plus dev-ae lines and a getpass/URL-encoding DATABASE_URL writer, field keys generated in a throwaway copy of dev's live release because dev-ae has no release before its first deploy and `migrate` refuses without keys, deploy key and secret, Turnstile hostname, first deploy, re-provision for the worker, checks).
- Merge order: until the server step and the `STAGING_SSH_KEY_DEV_AE` secret are done, the dev-ae leg is red on every master push; dev is unaffected.
- Verified: full suite 1545 passed, 3 known PostgreSQL-only failures; ci.yml parses with yaml.safe_load. Server step (hitl) not done.

## Plan Change Log

## Review Triage Log

- Pass 1 (quick): 6 findings; medium 2, low 3, false 1. Patched 5 (implementer, docs and workflow comments only).
  - medium, patch: dev-ae's backups under `staging-dev-ae/` would never expire; the bucket's only lifecycle rule filters `staging/` and the server user cannot delete. The runbook now sets both rules (the rule set is replaced as a whole) and reads them back.
  - medium, patch: dev-ae's settings.env is built from dev's file, and nothing checked for leftover `/srv/mytp-dev/` values. Added a count-only check (expects 0).
  - low, patch: each leg checks the tip of master on its own, so the two sites can briefly sit on different commits when master moves between the checks (the per-leg check is a recorded decision). Now documented in the workflow comment and the README.
  - low, patch: the README and workflow comments said in the present tense that every push deploys dev-ae. They now say the leg fails until the "Add dev-ae" runbook is done.
  - low, patch: one over-long README line rewrapped.
  - After patches: portal full suite 1545 passed (3 known PostgreSQL-only failures); ci.yml parses; test_deploy.py 73 passed.
  - false: "acceptance criteria not met". They need the server (hitl) step, which follows the review, as in 1.10.

## Verification

**Commands:**
- Portal full suite (non-secret test keys; venv `../.venv`; sandbox off) -- expected: all pass except the 3 known PostgreSQL-only tests.
- `python -c "import yaml;yaml.safe_load(open('.github/workflows/ci.yml'))"` -- expected: parses.

**Manual checks (if no CLI):**
- After the server step: `curl https://dev-ae.mytpdso.com/healthz` → `ok` / `site: dev-ae` / `region: ae`; dev's healthz unchanged; `/` behind the team password; one signup on dev-ae counted in its database only; `mytp-dev-ae-job@*` timers listed; kmscheck ok.

