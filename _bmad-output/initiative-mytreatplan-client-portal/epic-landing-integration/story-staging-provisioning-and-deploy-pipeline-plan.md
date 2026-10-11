---
title: 'Staging provisioning and deploy pipeline'
type: 'feature'
ticket: '13'
created: '2026-10-11'
status: 'built'
baseline_revision: 'c547672bb850b82ecbfe775fa963f9a479e22ebb'
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

**Problem:** Story 1.8 made the app safe to run behind HTTPS, but nothing serves it yet. There's no shared place where the team sees each merged change.

**Approach:** Add versioned server setup (an idempotent provisioning script plus Nginx and systemd files), a release script that only switches to a new release when everything succeeds, and a GitHub Actions job that deploys every push to `master` to https://dev.mytpdso.com, together with a runbook for the steps only a person can do.

## Boundaries & Constraints

**Always:**
- **Server:** `mytp-staging-1` (OVHcloud GRA11, b2-7, Debian 13, Python 3.13). The login user is `debian`. The app runs as a dedicated non-root user `mytp` from `/srv/mytp`, with `releases/<git-sha>/`, `current` → the active release, and `shared/` holding `settings.env` (`600`) and the DB CA file.
- **Each release** gets its own venv. `settings.env` is symlinked from `shared/` into the release, since settings read `BASE_DIR/settings.env`. `STATIC_ROOT` stays per release; Nginx serves `/srv/mytp/current/static_root/` as `/static/`.
- **Gunicorn** runs under systemd on a Unix socket. Nginx terminates HTTPS with a Let's Encrypt certificate for `dev.mytpdso.com` (certbot, auto-renew), redirects HTTP to HTTPS, sets `X-Forwarded-Proto`, and puts basic auth on everything except `/.well-known/acme-challenge/`.
- **Deploys** happen over SSH as `mytp` with a deploy-only key. `mytp` may run only `sudo systemctl restart mytp-gunicorn` (sudoers rule). No root SSH and no SSH passwords.
- **The firewall** allows only 22, 80 and 443.
- **`provision.sh` is idempotent** (a second run changes nothing) and runs on the server as `debian` with sudo. It takes the domain and the htpasswd user as arguments and asks for the basic auth password. Secrets are never echoed or written to the repo.
- **`release.sh`** runs on the server:
  - it unpacks the release and builds the venv;
  - it runs `compilemessages`, `collectstatic`, `migrate` and `createcachetable`;
  - only if all succeed does it switch `current`, restart Gunicorn and check health locally through the socket;
  - if the health check fails, it switches back and exits non-zero;
  - it keeps the last 5 releases.
- **The GitHub Actions job `deploy-staging`:** `needs: test`; runs only on push to `master`; uses environment `staging` and concurrency group `deploy-staging`. It builds the release tarball (`git archive` plus the built `static/css/site.css`), copies it over SSH and runs `release.sh`. Its secrets are `STAGING_HOST`, `STAGING_SSH_KEY` and `STAGING_KNOWN_HOSTS`, with no host key trust on first use.
- **Staging environment values** (set in `shared/settings.env` by the runbook):
  - `DEBUG=False`
  - `ALLOWED_HOSTS=dev.mytpdso.com`
  - `CSRF_TRUSTED_ORIGINS=https://dev.mytpdso.com`
  - `SITE_URL=https://dev.mytpdso.com`
  - `ROBOTS_NOINDEX=True`
  - `PORTAL_ENABLED=True`
  - `SECURE_HSTS_SECONDS=3600`
  - `DB_SSLMODE=verify-full`
  - `DB_SSLROOTCERT=/srv/mytp/shared/db-ca.pem`
  - `DATABASE_URL`, `SECRET_KEY`
- Decision (user, 2026-10-11): keep this plan whole (~2,100 tokens).
- Decision (user, 2026-10-11): staging URL `https://dev.mytpdso.com`; basic auth plus noindex; systemd, Gunicorn and Nginx; DB on its public endpoint with TLS and an IP allowlist; provisioning by a repo script the user runs; staging without confirmed disk encryption.

**Never:**
- Production (story 1.12). Backups, monitoring and cron jobs (story 1.9). Email (story 1.5).
- Secrets, private keys, htpasswd files or real hostnames' credentials in the repo.
- Deploying from pull requests or from branches other than `master`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Deploy | Push to `master`, CI green | New release live at https://dev.mytpdso.com; previous kept | — |
| Failed step | `migrate` (or any pre-switch step) fails | `current` unchanged; old release serving | Job fails with the step output |
| Failed health check | New release doesn't answer after restart | Switches back to the previous release, restarts | Job fails |
| Concurrent pushes | Two pushes close together | Deploys run one after another | — |
| Access | Request without the team password | 401; `/.well-known/acme-challenge/` still reachable | — |
| HTTP | `http://dev.mytpdso.com/x` | 301 to `https://dev.mytpdso.com/x` | — |
| Re-run provisioning | `provision.sh` run again | No changes, no errors | — |
| Pull request | CI on a PR | Tests run; no deploy | — |

</frozen-after-approval>

## Code Map

- `.github/workflows/ci.yml` -- job `test` (Python 3.13, Postgres 16, gettext, `npm ci && npm run build:css`, check, `makemigrations --check`, `migrate`, pytest; env `DEBUG=False`, `SECURE_SSL_REDIRECT=False`). Add the `deploy-staging` job.
- `mytreatplan/settings.py` -- reads env, then optional `BASE_DIR/settings.env`. Uses `STATIC_ROOT=BASE_DIR/'static_root'`, the 1.8 keys and `SITE_URL`. No change expected.
- `requirements.txt` -- pins `gunicorn==26.2.0`. Debian 13 ships Python 3.13; `gettext` is needed on the server for `compilemessages`.
- New `deploy/`:
  - `provision.sh`;
  - `release.sh`;
  - `nginx/mytp.conf.template` (`__DOMAIN__` placeholders);
  - `systemd/mytp-gunicorn.service` and `mytp-gunicorn.socket`;
  - `sudoers/mytp-deploy`;
  - `staging.env.example` (keys only; the template for `shared/settings.env`);
  - `README.md`, the runbook. It covers the human steps:
    1. DNS A record `dev.mytpdso.com` → the instance's IPv4.
    2. Create Managed PostgreSQL (GRA11): database `mytreatplan` and user, allowlist the instance IP, download the CA to `/srv/mytp/shared/db-ca.pem`.
    3. Run `provision.sh dev.mytpdso.com <htpasswd-user>`.
    4. Fill `shared/settings.env` from `deploy/staging.env.example`.
    5. Generate a deploy key, add its public half to `mytp`'s `authorized_keys`, and get the host key with `ssh-keyscan`.
    6. Create the GitHub environment `staging` with its three secrets.
    7. Push to `master` and verify.
    It also covers rollback (point `current` at the previous release, restart) and rotating the basic auth password.
- Tests: `deploy/` scripts get `bash -n`. A pytest test checks that the workflow YAML has the deploy job gated to push-on-`master` and the concurrency group (use `yaml.safe_load`; add PyYAML to `requirements-dev.txt` if missing).

## Tasks & Acceptance

**Execution:**
- [x] `deploy/systemd/*`, `deploy/nginx/mytp.conf.template`, `deploy/sudoers/mytp-deploy` -- config files as described.
- [x] `deploy/provision.sh` -- idempotent setup:
  - packages: nginx, certbot, python3-venv, gettext, ufw, apache2-utils for htpasswd;
  - the `mytp` user and directories;
  - the firewall;
  - installing the units, the Nginx site (from the template) and sudoers (checked with `visudo -c`);
  - htpasswd;
  - certbot `--nginx`, run only when DNS resolves to this host.
- [x] `deploy/release.sh` -- the release flow with the switch-only-on-success, health-check rollback and pruning.
- [x] `deploy/staging.env.example`, `deploy/README.md` -- the env template and the runbook.
- [x] `.github/workflows/ci.yml` -- the `deploy-staging` job.
- [x] Tests: `bash -n` over `deploy/*.sh`, and the workflow structure test.

**Acceptance Criteria:**
- Given the runbook steps are done, when a commit lands on `master`, then https://dev.mytpdso.com serves it within minutes, behind the password, with a valid certificate and the noindex header.
- Given a release whose migration fails, when it deploys, then the site keeps serving the previous release and the job is red.

## Implementation Notes

- Certificate: `certbot certonly --nginx` with `--deploy-hook "systemctl reload nginx"`, so certbot never edits the Nginx site (provision.sh owns it and stays idempotent). Until the cert exists only the HTTP part of the template (above `# __HTTPS_BEGIN__`) is installed.
- Extra packages beyond the list: `python3-certbot-nginx` (needed for `--nginx`), `libpq5` (psycopg 3 without `[binary]` needs it at runtime), `python3-pip`, `curl` (release health check).
- SSH hardening drop-in `/etc/ssh/sshd_config.d/00-mytp-hardening.conf` (no root login, no passwords) is written inline by provision.sh; `mytp` gets password `*` (not locked, so key login works).
- Gunicorn uses systemd socket activation (`mytp-gunicorn.socket`, owner `mytp`, group `www-data`, 0660), so restarts queue requests and `release.sh` can health-check through the socket.
- Health check: `GET /robots.txt` through the socket with `Host:` = first `ALLOWED_HOSTS` entry and `X-Forwarded-Proto: https`; 15 tries, 2 s apart.
- CI copies `deploy/release.sh` from the deployed commit to `/srv/mytp/incoming/<sha>-release.sh` together with the tarball, then runs it over SSH (host alias written to `~/.ssh/config` with `StrictHostKeyChecking yes`).
- Pruning keeps the newest 5 releases plus the live and previous ones. Re-deploying the live sha only re-checks health.
- Workflow-structure tests use `pytest.importorskip('yaml')`; PyYAML 6.0.3 added to requirements-dev.txt.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, 2026-10-11): high 0, medium 2, low 4, false 0, maybe-false 0.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Concurrency serialises deploys but not in push order; an older commit can go live after a newer one | medium | patch | Confirmed: each deploy waits on its own test job; `release.sh` only skips the live SHA. Patch: the deploy step skips when `$SHA` isn't the current tip of `master`; README and test updated. |
| 2 | `compilemessages` from the release root walks `venv/` and recompiles every dependency catalogue | medium | patch | Reviewer read Django 6.1's `compilemessages` (`os.walk('.')`). Slow and fragile deploys. Patch: `--ignore venv`. |
| 3 | A release that failed its health check keeps a prune slot | low | patch | Confirmed. Patch: remove it after rollback. |
| 4 | ufw re-runs don't remove pre-existing extra ALLOW rules | low | rejected | The fresh OVHcloud instance has no prior rules; enforcing it means resetting ufw on every run (more complexity and lock-out risk). |
| 5 | HTTP redirect uses the client `$host` | low | patch | Confirmed in the template. Patch: redirect to `__DOMAIN__`. |
| 6 | DNS-mismatch warning split across stdout and stderr | low | patch | Confirmed. Patch: all to stderr. |

## Verification

**Commands:**
- `bash -n deploy/provision.sh deploy/release.sh` -- expected: no errors
- `pytest` -- expected: all pass
- `python manage.py check` -- expected: no issues

**Manual checks (if no CLI):**
- The full runbook on `mytp-staging-1`: push to `master`; https://dev.mytpdso.com asks for the password, has a valid certificate and shows the site; `X-Robots-Tag` is present; `provision.sh` run again reports no changes; a deliberately failing migration leaves the old release serving.
