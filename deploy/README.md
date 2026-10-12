# Staging: server setup and deploys

Every push to `master` that passes CI is deployed to **https://dev.mytpdso.com**
by the `deploy-staging` job in `.github/workflows/ci.yml`. The site sits behind a
team password (basic auth) and sends `X-Robots-Tag: noindex, nofollow`.

| What | Where |
|------|-------|
| Server | `mytp-staging-1` (OVHcloud GRA11, b2-7, Debian 13, Python 3.13), login user `debian` |
| App user | `mytp` (no password; SSH with the deploy key only) |
| Releases | `/srv/mytp/releases/<git-sha>/` (each with its own `venv/` and `static_root/`) |
| Live release | `/srv/mytp/current` → one of the releases |
| Settings | `/srv/mytp/shared/settings.env` (mode 600), linked into every release |
| DB CA | `/srv/mytp/shared/db-ca.pem` |
| App service | `mytp-gunicorn.service` on socket `/run/mytp-gunicorn.sock` (`mytp-gunicorn.socket`) |
| Web server | Nginx, `/etc/nginx/sites-available/mytp.conf`, password file `/etc/nginx/mytp.htpasswd` |
| Certificate | Let's Encrypt via certbot; renewed by `certbot.timer` |
| Firewall | ufw: 22, 80, 443 only |
| Scheduled jobs | `mytp-job@<name>.timer` → `mytp-job@<name>.service`, one per line of `jobs.conf` |
| Health URL | `https://dev.mytpdso.com/healthz` (no password), watched by UptimeRobot |
| Errors and job alerts | Sentry (EU region), alerts to devops@mytreatplan.com |

Files in this folder:

- `provision.sh`: idempotent server setup (packages, SSH hardening, firewall, `mytp` user
  and directories, systemd units, scheduled job timers, sudoers, basic auth, Nginx,
  certificate).
- `release.sh`: installs one release; CI copies it to the server and runs it.
- `jobs.conf`: the scheduled jobs (name, `manage.py` command, schedule).
- `run-job.sh`: runs one job in the live release (started by `mytp-job@.service`).
- `nginx/mytp.conf.template`, `systemd/*` (including `mytp-job.timer.template`),
  `sudoers/mytp-deploy`: installed by `provision.sh`.
- `staging.env.example`: template for `shared/settings.env`.

## First-time setup (human steps)

Never put secrets, keys or the password file in the repo.

### 1. DNS

Create an A record `dev.mytpdso.com` → the instance's public IPv4. Wait until
`dig +short dev.mytpdso.com` returns it (step 3 only requests the certificate once it does).

### 2. Database (OVHcloud Managed PostgreSQL, GRA11)

1. Create the service, a database `mytreatplan` and a user for the app.
2. Add the instance's public IPv4 to the service's IP allowlist.
3. Download the service CA certificate. After step 3 has created `/srv/mytp/shared`, copy it
   to the server:

   ```sh
   scp ca.pem debian@<instance-ip>:/tmp/db-ca.pem
   ssh debian@<instance-ip> 'sudo install -m 640 -o mytp -g mytp /tmp/db-ca.pem /srv/mytp/shared/db-ca.pem && rm /tmp/db-ca.pem'
   ```

### 3. Provision the server

```sh
scp -r deploy debian@<instance-ip>:~/
ssh debian@<instance-ip>
CERTBOT_EMAIL=<ops-address> bash ~/deploy/provision.sh dev.mytpdso.com <htpasswd-user>
```

It asks for the basic auth password (not echoed). `CERTBOT_EMAIL` is optional (expiry
notices). Running it again must end with **"No changes."**; if DNS was not ready, it skips
the certificate with a warning, so fix DNS and run it again.

Make sure you can still log in as `debian` with your key in a second terminal before
closing the first one: the script turns off SSH passwords and root login.

### 4. Settings

```sh
sudo install -m 600 -o mytp -g mytp ~/deploy/staging.env.example /srv/mytp/shared/settings.env
sudo -u mytp nano /srv/mytp/shared/settings.env
```

Fill in `SECRET_KEY` (e.g. `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`)
and `DATABASE_URL` (from step 2), and `SENTRY_DSN` once step 8 gives you one (empty
leaves Sentry off). Leave the other values as they are.

### 5. Deploy key and host key

On your machine:

```sh
ssh-keygen -t ed25519 -N '' -C 'github-actions deploy-staging' -f ./mytp_deploy_staging
```

Add the public half to `mytp`'s keys on the server:

```sh
cat mytp_deploy_staging.pub | ssh debian@<instance-ip> 'sudo tee -a /srv/mytp/.ssh/authorized_keys >/dev/null'
```

The key belongs to `mytp` only. Then get the host key
**from a trusted network path** and compare its fingerprint with the one in the OVHcloud
console or `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server:

```sh
ssh-keyscan -t ed25519 <instance-ip> > staging_known_hosts
ssh-keygen -lf staging_known_hosts
```

Test: `ssh -i ./mytp_deploy_staging -o UserKnownHostsFile=./staging_known_hosts mytp@<instance-ip> true`.

### 6. GitHub environment

Repository → Settings → Environments → New environment `staging` (optionally restrict
deployments to the `master` branch). Add three environment secrets:

| Secret | Value |
|--------|-------|
| `STAGING_HOST` | the instance's IPv4 (the same name/IP used in `ssh-keyscan`) |
| `STAGING_SSH_KEY` | contents of `mytp_deploy_staging` (private key) |
| `STAGING_KNOWN_HOSTS` | contents of `staging_known_hosts` |

Then delete the local private key copy (`mytp_deploy_staging`); a new one can be made at any time.

### 7. First deploy and checks

Push (merge) to `master`. When the `deploy-staging` job is green:

```sh
curl -sI https://dev.mytpdso.com/en/                    # 401 (no password)
curl -sI -u <user> https://dev.mytpdso.com/en/          # 200, has X-Robots-Tag: noindex, nofollow
curl -sI http://dev.mytpdso.com/x                       # 301 to https://dev.mytpdso.com/x
curl -sI http://dev.mytpdso.com/.well-known/acme-challenge/test  # 404, not 401
```

Open the site in a browser: valid certificate, password prompt, pages render with styles.

```sh
curl -s https://dev.mytpdso.com/healthz                  # ok (no password needed)
```

### 8. Sentry (errors and scheduled jobs)

1. In Sentry, use (or create) an organisation whose **data storage region is EU**
   (Germany). The region is chosen when the organisation is created and cannot change.
2. Create a project: platform **Django**, name `mytreatplan`. Copy its DSN from Project
   Settings → Client Keys. It must point at `*.de.sentry.io`
   (`https://<key>@o<org>.ingest.de.sentry.io/<project>`).
3. Project Settings → Security & Privacy: turn on **Data Scrubber** and
   **Prevent Storing of IP Addresses** (the app already strips personal data before
   sending; this is a second layer).
4. Alerts → Create Alert → Issues, with the conditions "A new issue is created" **or**
   "The issue changes state from resolved to unresolved" (a regression), environment
   `staging` → send an email to **devops@mytreatplan.com**. Add the address as a team member or use
   Settings → Notifications so it can receive mail.
5. Crons: nothing to create by hand. The first run of each job creates its monitor
   (`mytp-staging-<name>`, schedule taken from `jobs.conf`, 30 min margin, 60 min max
   runtime). After the first run, open Crons → the monitor → Edit and set
   **Notify** to devops@mytreatplan.com (failed, missed and timed-out check-ins).
6. On the server, put the DSN in `/srv/mytp/shared/settings.env`:

   ```sh
   sudo -u mytp nano /srv/mytp/shared/settings.env   # SENTRY_DSN=https://...ingest.de.sentry.io/...
   sudo systemctl restart mytp-gunicorn
   ```

   `SENTRY_ENVIRONMENT=staging` is already there. The release is the git SHA (each
   release's `REVISION` file, written by `release.sh`).
7. Check: force an error and see it arrive (no personal data) and the email go out:

   ```sh
   cd /srv/mytp/current
   sudo -u mytp ./venv/bin/python manage.py shell -c "import sentry_sdk
   try:
       1 / 0
   except ZeroDivisionError:
       sentry_sdk.capture_exception()
   sentry_sdk.flush()"
   ```

   Then check the jobs (after `provision.sh` has installed the timers):

   ```sh
   sudo systemctl start mytp-job@clearsessions          # Sentry Crons: ok check-in
   # A failing run (unreachable database; the env var beats settings.env):
   sudo -u mytp DATABASE_URL=postgres://x@127.0.0.1:1/x ./venv/bin/python manage.py runjob clearsessions
   ```

   The second command exits non-zero and Sentry records an error check-in plus an issue;
   devops@mytreatplan.com gets the alert. The next scheduled run turns the monitor ok again.

Personal data never goes to Sentry: `mytreatplan/sentry.py` sets `send_default_pii=False`,
sends no request bodies or local variables, and strips cookies, headers, query strings,
user email and IP in `before_send`. Locally and in CI `SENTRY_DSN` is unset, so nothing is sent.

### 9. UptimeRobot

1. Create the UptimeRobot account with **devops@mytreatplan.com** (or add it as an alert
   contact) and confirm the address from the email it sends; keep the login in the team
   password manager.
2. New monitor: type **HTTP(s)** (not Ping or Port: those stay green when only Gunicorn is
   down), URL `https://dev.mytpdso.com/healthz`, interval 5 min, alert contact
   devops@mytreatplan.com. Optionally a **Keyword** monitor for `ok`. Wait until it shows Up.
3. Check: `sudo systemctl stop mytp-gunicorn.service mytp-gunicorn.socket`, then confirm
   from your machine that `curl -s -o /dev/null -w '%{http_code}\n'
   https://dev.mytpdso.com/healthz` prints `502`. Keep it stopped for **at least 10
   minutes** (two check intervals plus UptimeRobot's re-check) until the down alert
   arrives; then `sudo systemctl start mytp-gunicorn.socket` (the service starts on the
   next request) → an up alert. Staging is unavailable to the team meanwhile.

`/healthz` answers `ok` (200) when the app can run `SELECT 1` on the database, else
`unavailable` (503). It reveals nothing else, and is the only path without the password.

## How a deploy works

1. CI (`test`) passes on a push to `master`. Pull requests and other branches never deploy.
2. `deploy-staging` builds `release.tar.gz` (`git archive` of the commit plus the built
   `static/css/site.css`). If `master` has moved on to a newer commit by then, the job
   ends green without deploying (the newer push deploys itself). Otherwise it copies the
   tarball and `deploy/release.sh` to `/srv/mytp/incoming/`.
3. `release.sh` (as `mytp`) unpacks to `releases/<sha>/`, links `settings.env`, builds the
   venv, runs `compilemessages`, `collectstatic`, `migrate`, `createcachetable`.
   If any step fails, the half-built release is removed, `current` is unchanged and the job
   is red with that step's output.
4. It switches `current`, runs `sudo systemctl restart mytp-gunicorn` (the only sudo
   right `mytp` has) and requests `/robots.txt` through the socket. If that does not
   answer 200, it switches back to the previous release, restarts, and the job is red.
5. It keeps the newest 5 releases (plus the live and previous one).

Deploys run one at a time (concurrency group `deploy-staging`). If several pushes queue
up, GitHub keeps only the newest pending one, and a job whose commit is no longer the tip
of `master` skips the deploy, so the latest commit is what goes live even when an older
push's tests finish last.

Note: migrations run before the switch, so the previous release briefly runs against the
new schema, and a failed health check does not undo migrations. Keep migrations
backwards-compatible.

## Scheduled jobs

Jobs are listed in `deploy/jobs.conf`, one per line: `name | manage.py command | schedule`.
The schedule is a systemd `OnCalendar` in UTC, e.g. `*-*-* 03:30:00 UTC` (daily) or
`Mon *-*-* 02:00 UTC` (weekly); `mytp_publicsite/jobs.py` lists the forms supported
(they must also map to the Sentry monitor's crontab, and the tests check every line).

`provision.sh` installs the timers and a copy of the `jobs.conf` it used at
`/srv/mytp/shared/jobs.conf`. `runjob` reads the job list and schedules from that copy
(falling back to the release's own file only when it is missing, e.g. locally), so the
Sentry monitors always match the timers. A deploy that changes `jobs.conf` does not change
the timers: `release.sh` prints a warning, and you must re-run `provision.sh`.

Each job runs as `mytp` in `/srv/mytp/current` via `deploy/run-job.sh <name>` →
`manage.py runjob <name>`, which sends Sentry cron check-ins (in progress, then ok or
error). A missed run (host down, timer gone) shows as a missed check-in in Sentry; a
failed run exits non-zero, so the unit is marked failed. Timers are `Persistent=true`:
a run missed while the host was down happens at boot.

To add a job:

1. Add a line to `jobs.conf` (the command must exist in the app) and merge it.
2. On the server, from a copy of the merged `deploy/`, run `bash ~/deploy/provision.sh
   dev.mytpdso.com <htpasswd-user>`. It installs and starts the new timer, updates
   changed schedules and removes timers no longer listed.
3. After its first run, set the Sentry monitor's notifications (step 8.5).

To remove a job: delete its line, merge, re-run `provision.sh` (it stops and removes the
timer), then in Sentry → Crons delete (or disable) the monitor `mytp-staging-<name>`;
otherwise it keeps raising missed check-in alerts.

Run a job by hand / read its logs:

```sh
systemctl list-timers 'mytp-job@*'             # next and last run of each job
sudo systemctl start mytp-job@clearsessions    # run now (waits until it finishes)
systemctl status mytp-job@clearsessions        # last result
sudo journalctl -u mytp-job@clearsessions -n 100
systemctl list-units --failed 'mytp-job@*'
```

## Rollback

On the server as `debian`:

```sh
ls -1t /srv/mytp/releases/          # newest first
readlink /srv/mytp/current          # the live one
sudo -u mytp ln -sfn /srv/mytp/releases/<previous-sha> /srv/mytp/current
sudo systemctl restart mytp-gunicorn
```

This does not reverse database migrations. The next push to `master` deploys over it.
To re-deploy a commit, re-run its `deploy-staging` job in GitHub Actions.

## Rotate the basic auth password

```sh
sudo htpasswd -B /etc/nginx/mytp.htpasswd <htpasswd-user>   # prompts, not echoed
```

Nginx reads the file on each request; no reload needed. To add a person, run the same
command with a new user name; to remove one, `sudo htpasswd -D /etc/nginx/mytp.htpasswd <user>`.

## Rotate the deploy key

Make a new key (step 5), replace the line in `/srv/mytp/.ssh/authorized_keys`, and
update `STAGING_SSH_KEY`. If the server is rebuilt, also refresh `STAGING_KNOWN_HOSTS`.

## Logs

```sh
sudo journalctl -u mytp-gunicorn -n 200    # app (Gunicorn) logs
sudo journalctl -u 'mytp-job@*' -n 200     # scheduled jobs
sudo tail -f /var/log/nginx/error.log
```

Out of scope here: production (story 1.12); backups (story 1.14); KMS (story 1.15);
email (story 1.5).
