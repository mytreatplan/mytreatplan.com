# Staging: server setup and deploys

Every push to `master` that passes CI is deployed to **https://dev.mytpdso.com**, the
staging host's `dev` site, and to **https://dev-ae.mytpdso.com**, the `dev-ae` site (the
same build with `REGION=ae`), by the `deploy-staging` job in `.github/workflows/ci.yml`
(one matrix leg per site). The dev-ae leg fails until the [Add dev-ae](#add-dev-ae)
runbook is done; dev deploys either way. The sites sit behind a team password (basic auth) and send `X-Robots-Tag: noindex, nofollow`.

The staging host runs several **sites** side by side (`dev`, `dev-ae` and `admindev`,
the admin repo's site; architecture AD-12), each with its own user, home, units, timers,
Nginx site and database. This file uses `dev`; for another site replace `dev` in every
name (see [Sites on the staging host](#sites-on-the-staging-host)).

| What | Where |
|------|-------|
| Server | `mytp-staging-1` (OVHcloud GRA11, b2-7, Debian 13, Python 3.13), login user `debian` |
| Site | `dev` (`SITE=dev` in its `settings.env`) |
| App user | `mytp-dev` (no password; SSH with the deploy key only) |
| Releases | `/srv/mytp-dev/releases/<git-sha>/` (each with its own `venv/` and `static_root/`) |
| Live release | `/srv/mytp-dev/current` → one of the releases |
| Settings | `/srv/mytp-dev/shared/settings.env` (mode 600), linked into every release |
| DB CA | `/srv/mytp-dev/shared/db-ca.pem` |
| App service | `mytp-dev-gunicorn.service` on socket `/run/mytp-dev-gunicorn.sock` (`mytp-dev-gunicorn.socket`) |
| Web server | Nginx, `/etc/nginx/sites-available/mytp-dev.conf`, password file `/etc/nginx/mytp-dev.htpasswd` |
| Deploy rights | `/etc/sudoers.d/mytp-dev-deploy`: `mytp-dev` may only restart `mytp-dev-gunicorn` and stop or restart `mytp-dev-worker` |
| Release command | `/usr/local/bin/mytp-release <site> <sha> <tarball>` (host-wide, from `release.sh`) |
| Certificate | Let's Encrypt via certbot; renewed by `certbot.timer` |
| Firewall | ufw: 22, 80, 443 only |
| Scheduled jobs | `mytp-dev-job@<name>.timer` → `mytp-dev-job@<name>.service`, one per line of `jobs.conf` |
| Background tasks | `mytp-dev-worker.service` (`manage.py db_worker`, django-tasks-db); heartbeat on Sentry monitor `mytp-dev-worker` ("Background tasks") |
| Health URL | `https://dev.mytpdso.com/healthz` (no password), watched by UptimeRobot |
| Object storage | doctors' files in the region's bucket (`mytp-dev-files`, GRA), `STORAGE_*` in `shared/settings.env`; `storage_bucket --apply/--inspect`; staff sample page `/en/storage/sample/` ("Object storage") |
| Off-region backups | nightly `backupdb` job → Object Storage SBG (`age`-encrypted); restore test: `restore-db.sh` |
| Field keys | KMS-wrapped: `FIELD_KMS_KEYS`, `BLIND_INDEX_KMS_KEY` in `shared/settings.env` (`FIELD_KEY_PROVIDER=accounts.crypto.KMSKeyProvider`); password manager: key ids and wrapped values, never plain keys (step 12, "Keys (KMS)") |
| Keys (KMS) | OVHcloud KMS (France), staging service key; access certificate in `/srv/mytp-dev/shared/kms-client.*`; daily `kmscheck` job |
| Signup and login codes | Twilio Verify (email via SendGrid, WhatsApp, SMS); credentials in `shared/settings.env` ("Twilio Verify") |
| Geo-IP | DB-IP Lite country database `/srv/mytp-dev/shared/geoip/dbip-country-lite.mmdb` (CC BY 4.0), monthly `geoipupdate` job ("Geo-IP database") |
| Interface with the admin site | signed `/api/v1/` (no password), `INTERFACE_*` in `shared/settings.env`, `manage.py ping_peer admindev`, call log kept 90 days; outbox of events to the admin site, `dispatchoutbox` job every minute; configuration snapshot pulled from it, `pullconfig` job every 5 minutes, `show_config`; the catalogue it carries, `price_catalogue` ("Interface with the admin site", "Outbox", "Configuration snapshot", "Catalogue") |
| Errors and job alerts | Sentry (EU region), alerts to devops@mytreatplan.com |

Files in this folder:

- `provision.sh <site> <domain> <htpasswd-user>`: idempotent setup of one site. Host-wide:
  packages, SSH hardening, firewall, certificate renewal and the release command.
  Per site: user and directories, systemd units (Gunicorn and the task worker), scheduled
  job timers, sudoers, basic auth, Nginx, certificate.
- `release.sh`: installs one release of one site; `provision.sh` installs it as
  `/usr/local/bin/mytp-release`, which CI runs.
- `jobs.conf`: the scheduled jobs (name, `manage.py` command, schedule).
- `run-job.sh <site> <name>`: runs one job in the site's live release (started by
  `mytp-<site>-job@.service`).
- `restore-db.sh`: the restore test for the off-region backups (run on a team Mac).
- `nginx/site.conf.template`, `systemd/*.template`, `sudoers/deploy.template`: rendered
  per site by `provision.sh` (`__SITE__` becomes the site name).
- `staging.env.example`: template for `shared/settings.env` (written for `dev`).

## First-time setup (human steps)

Never put secrets, keys or the password file in the repo.

### 1. DNS

Create an A record `dev.mytpdso.com` → the instance's public IPv4. Wait until
`dig +short dev.mytpdso.com` returns it (step 3 only requests the certificate once it does).

### 2. Database (OVHcloud Managed PostgreSQL, GRA11)

1. Create the service, a database `mytreatplan` and a user for the app.
2. Add the instance's public IPv4 to the service's IP allowlist.
3. Download the service CA certificate. After step 3 has created `/srv/mytp-dev/shared`, copy it
   to the server:

   ```sh
   scp ca.pem debian@<instance-ip>:/tmp/db-ca.pem
   ssh debian@<instance-ip> 'sudo install -m 640 -o mytp-dev -g mytp-dev /tmp/db-ca.pem /srv/mytp-dev/shared/db-ca.pem && rm /tmp/db-ca.pem'
   ```

### 3. Provision the server

```sh
scp -r deploy debian@<instance-ip>:~/
ssh debian@<instance-ip>
CERTBOT_EMAIL=<ops-address> bash ~/deploy/provision.sh dev dev.mytpdso.com <htpasswd-user>
```

It asks for the basic auth password (not echoed). `CERTBOT_EMAIL` is optional (expiry
notices). Running it again must end with **"No changes."**; if DNS was not ready, it skips
the certificate with a warning, so fix DNS and run it again.

Make sure you can still log in as `debian` with your key in a second terminal before
closing the first one: the script turns off SSH passwords and root login.

### 4. Settings

```sh
sudo install -m 600 -o mytp-dev -g mytp-dev ~/deploy/staging.env.example /srv/mytp-dev/shared/settings.env
sudo -u mytp-dev nano /srv/mytp-dev/shared/settings.env
```

Keep `SITE=dev` (the release command refuses a `settings.env` whose `SITE` is not the
site it deploys). Fill in `SECRET_KEY` (e.g. `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`)
and `DATABASE_URL` (from step 2), and `SENTRY_DSN` once step 8 gives you one (empty
leaves Sentry off). Leave the other values as they are.

### 5. Deploy key and host key

On your machine:

```sh
ssh-keygen -t ed25519 -N '' -C 'github-actions deploy-staging' -f ./mytp_deploy_staging
```

Add the public half to `mytp-dev`'s keys on the server:

```sh
cat mytp_deploy_staging.pub | ssh debian@<instance-ip> 'sudo tee -a /srv/mytp-dev/.ssh/authorized_keys >/dev/null'
```

The key belongs to `mytp-dev` only. Then get the host key
**from a trusted network path** and compare its fingerprint with the one in the OVHcloud
console or `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server:

```sh
ssh-keyscan -t ed25519 <instance-ip> > staging_known_hosts
ssh-keygen -lf staging_known_hosts
```

Test: `ssh -i ./mytp_deploy_staging -o UserKnownHostsFile=./staging_known_hosts mytp-dev@<instance-ip> true`.

### 6. GitHub environment

Repository → Settings → Environments → New environment `staging` (optionally restrict
deployments to the `master` branch). Add three environment secrets:

| Secret | Value |
|--------|-------|
| `STAGING_HOST` | the instance's IPv4 (the same name/IP used in `ssh-keyscan`) |
| `STAGING_SSH_KEY` | contents of `mytp_deploy_staging` (private key) |
| `STAGING_KNOWN_HOSTS` | contents of `staging_known_hosts` |

Then delete the local private key copy (`mytp_deploy_staging`); a new one can be made at any time.

`STAGING_SSH_KEY` is dev's key. Each other portal site has its own key and secret, picked
by its matrix leg in `deploy-staging` (`STAGING_SSH_KEY_DEV_AE` for dev-ae, see
[Add dev-ae](#add-dev-ae)).

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
curl -s https://dev.mytpdso.com/healthz                  # ok, site: dev, region: eu (no password needed)
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
   (`mytp-<site>-<name>`, e.g. `mytp-dev-clearsessions`, schedule taken from `jobs.conf`, 30 min margin, 60 min max
   runtime). After the first run, open Crons → the monitor → Edit and set
   **Notify** to devops@mytreatplan.com (failed, missed and timed-out check-ins).
6. On the server, put the DSN in `/srv/mytp-dev/shared/settings.env`:

   ```sh
   sudo -u mytp-dev nano /srv/mytp-dev/shared/settings.env   # SENTRY_DSN=https://...ingest.de.sentry.io/...
   sudo systemctl restart mytp-dev-gunicorn mytp-dev-worker
   ```

   `SENTRY_ENVIRONMENT=staging` is already there. The release is the git SHA (each
   release's `REVISION` file, written by `release.sh`).
7. Check: force an error and see it arrive (no personal data) and the email go out:

   ```sh
   cd /srv/mytp-dev/current
   sudo -u mytp-dev ./venv/bin/python manage.py shell -c "import sentry_sdk
   try:
       1 / 0
   except ZeroDivisionError:
       sentry_sdk.capture_exception()
   sentry_sdk.flush()"
   ```

   Then check the jobs (after `provision.sh` has installed the timers):

   ```sh
   sudo systemctl start mytp-dev-job@clearsessions          # Sentry Crons: ok check-in
   # A failing run (unreachable database; the env var beats settings.env):
   sudo -u mytp-dev DATABASE_URL=postgres://x@127.0.0.1:1/x ./venv/bin/python manage.py runjob clearsessions
   ```

   The second command exits non-zero and Sentry records an error check-in plus an issue;
   devops@mytreatplan.com gets the alert. The next scheduled run turns the monitor ok again.

   The retention jobs (signup entry 10) are `purgepending` (hourly at :15, `manage.py
   purge_pending_signups`: deletes signups left unfinished for 24 h, ends their event
   log attempts as `timeout`, deletes expired cache rows) and `eventlogretention` (daily
   at 03:45 UTC, `manage.py eventlog_retention`: clears the event log's personal values
   30 days after an attempt ends, deletes attempts 2 years after they end; login and
   page records (epic-login entry 8) are never anonymized, keep their IP and User-Agent,
   and are deleted 5 calendar years after they end, also after their account is purged).
   On a server provisioned before them, run `provision.sh` again (see
   [Scheduled jobs](#scheduled-jobs)) to install their timers, then check:

   ```sh
   systemctl list-timers 'mytp-dev-job@*'                # lists purgepending and eventlogretention
   sudo systemctl start mytp-dev-job@purgepending        # Sentry Crons: mytp-dev-purgepending ok
   sudo systemctl start mytp-dev-job@eventlogretention   # Sentry Crons: mytp-dev-eventlogretention ok
   sudo journalctl -u mytp-dev-job@purgepending -n 20    # counts only, no personal values
   sudo journalctl -u mytp-dev-job@eventlogretention -n 20
   ```

   Then set both monitors' notifications in Sentry → Crons (step 8.5).

Personal data never goes to Sentry: `mytp_core.sentry` (the mytp-core package) sets
`send_default_pii=False`, sends no request bodies or local variables, and in
`before_send` strips cookies, headers, query strings, user email and IP, redacts emails,
IPs and phone numbers in messages and data, and replaces the values of named sensitive
fields (password, token, key, email, phone, name, patient, address, iban, ...) in extra
data, contexts, tags and breadcrumbs. The `signup.verification` logger never reaches
Sentry (`MYTP_SENTRY_IGNORED_LOGGERS` in settings). Locally and in CI `SENTRY_DSN` is unset, so nothing is sent.

### 9. UptimeRobot

1. Create the UptimeRobot account with **devops@mytreatplan.com** (or add it as an alert
   contact) and confirm the address from the email it sends; keep the login in the team
   password manager.
2. New monitor: type **HTTP(s)** (not Ping or Port: those stay green when only Gunicorn is
   down), URL `https://dev.mytpdso.com/healthz`, interval 5 min, alert contact
   devops@mytreatplan.com. Optionally a **Keyword** monitor for `ok`. Wait until it shows Up.
3. Check: `sudo systemctl stop mytp-dev-gunicorn.service mytp-dev-gunicorn.socket`, then confirm
   from your machine that `curl -s -o /dev/null -w '%{http_code}\n'
   https://dev.mytpdso.com/healthz` prints `502`. Keep it stopped for **at least 10
   minutes** (two check intervals plus UptimeRobot's re-check) until the down alert
   arrives; then `sudo systemctl start mytp-dev-gunicorn.socket` (the service starts on the
   next request) → an up alert. Staging is unavailable to the team meanwhile.

`/healthz` answers `ok` and `site: <site>` (200) when the app can run `SELECT 1` on the
database, else `unavailable` (503); the first line is always `ok` or `unavailable`. It reveals nothing else, and is the only path without the password.

### 10. Off-region backups

Two layers: OVHcloud's managed backups (same region as the database, GRA) and a nightly
encrypted `pg_dump` to Object Storage in **SBG** (`manage.py backupdb`). See
[Backups and restore](#backups-and-restore) for how they work.

1. **Managed backups.** OVHcloud Control Panel → Public Cloud → Databases → the
   PostgreSQL service → *Backups*: check that daily backups are listed, and note the
   retention and the backup location shown for the plan. Write both in the table under
   "Backups and restore" (they depend on the plan; nothing here changes them).
2. **Admin S3 user and bucket.** Public Cloud → Users & Roles → add a user, e.g.
   `mytp-backup-admin` (role *ObjectStore operator*); Object Storage → *S3 users* →
   generate its S3 credentials and keep them in the team password manager (they set the
   lifecycle rule; never put them on the server). Then Object Storage → *Create object
   container*: S3 API, region **SBG** (Strasbourg), private, name e.g.
   `mytp-backups-staging`, and **link `mytp-backup-admin` as its owner** in the wizard.
   Only the bucket owner can set its lifecycle rule: with another user linked, step 3
   fails with `AccessDenied` even under an `s3:*` policy. Copy the S3 endpoint
   (e.g. `https://s3.sbg.io.cloud.ovh.net`) and region (`sbg`).
3. **Retention: 30 daily dumps.** Add a lifecycle rule that expires objects after 30
   days. In your own terminal (keys never pasted into chats or tickets), with the admin
   user's keys in `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (never the server's user):

   ```sh
   export AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
   aws --endpoint-url https://s3.sbg.io.cloud.ovh.net --region sbg s3api put-bucket-lifecycle-configuration \
       --bucket mytp-backups-staging --lifecycle-configuration '{"Rules": [{
         "ID": "expire-dumps-30d", "Status": "Enabled", "Filter": {"Prefix": "staging/"},
         "Expiration": {"Days": 30},
         "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 2}}]}'
   aws --endpoint-url https://s3.sbg.io.cloud.ovh.net --region sbg s3api get-bucket-lifecycle-configuration \
       --bucket mytp-backups-staging
   ```

   **If SBG refuses the rule or does not support expiration, stop here and raise it with
   the team** before going on: `backupdb` never deletes, so without the rule the bucket
   grows forever, and the server's key must not get delete rights to work around it.
4. **S3 user for the server (write and read, no delete).** Public Cloud → Users & Roles →
   add a user (role *ObjectStore operator*), then Object Storage → *S3 users* → generate
   its S3 credentials and *import* this policy (replace the bucket name):

   ```json
   {
     "Statement": [{
       "Sid": "BackupWriteReadNoDelete",
       "Effect": "Allow",
       "Action": [
         "s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation",
         "s3:ListBucketMultipartUploads", "s3:ListMultipartUploadParts",
         "s3:AbortMultipartUpload"
       ],
       "Resource": ["arn:aws:s3:::mytp-backups-staging", "arn:aws:s3:::mytp-backups-staging/*"]
     }]
   }
   ```

   No `s3:DeleteObject`. This does not make old dumps safe from the server: `s3:PutObject`
   can still overwrite an existing object. Bucket versioning or object lock would close
   that; decide on it for production (story 1.12). Keep the access and secret key in the team password manager
   (they go on the server in step 6). Restore tests may use the same keys from the
   password manager, or a separate read-only user.
5. **Encryption key (age).** On a team Mac (`brew install age`):

   ```sh
   age-keygen -o mytp-backup-staging.key     # prints "Public key: age1..."
   ```

   Store the **whole file** (it holds the private identity) in the team password manager
   as "MyTreatplan staging backup key (age)", then delete the local file
   (`rm mytp-backup-staging.key`). The private key never goes to the server: without it no
   dump can be read, and if it is lost every dump is useless, so check the password
   manager entry before deleting the file. The `age1...` public key is
   `BACKUP_AGE_RECIPIENT`.
6. **Settings and provision.** On the server, fill in the `BACKUP_*` keys in
   `/srv/mytp-dev/shared/settings.env` (see `deploy/staging.env.example`). One `KEY=value`
   per line with **no comment after the value**: the settings loader keeps a trailing
   `# ...` as part of the value, and the upload then fails (`S3UploadFailedError`).

   ```sh
   sudo -u mytp-dev nano /srv/mytp-dev/shared/settings.env
   ```

   Then, from a copy of the merged `deploy/`, re-run
   `bash ~/deploy/provision.sh dev dev.mytpdso.com <htpasswd-user>`: it installs
   `postgresql-client` and `age`, creates `/srv/mytp-dev/shared/backup-tmp` (0700, `mytp-dev`)
   and the `mytp-dev-job@backupdb.timer` (02:30 UTC). Check `pg_dump --version` says 17 (the
   managed service's major version; `backupdb` refuses an older client).
7. **First backup by hand.**

   ```sh
   sudo systemctl start mytp-dev-job@backupdb           # waits until it finishes
   sudo journalctl -u mytp-dev-job@backupdb -n 20       # "backupdb: uploaded staging/mytreatplan-...dump.age"
   ```

   The object appears in the SBG container. Downloaded, it starts with
   `age-encryption.org/v1` and is unreadable without the key. In Sentry, Crons now has
   `mytp-dev-backupdb` with an ok check-in: set its **Notify** to
   devops@mytreatplan.com (step 8.5).
8. **Alert check.** A run with wrong bucket credentials must fail and alert (the env var
   beats `settings.env`):

   ```sh
   cd /srv/mytp-dev/current
   sudo -u mytp-dev BACKUP_S3_SECRET_KEY=wrong ./venv/bin/python manage.py runjob backupdb
   ```

   It exits non-zero; Sentry shows an error check-in and an issue (without the keys), and
   devops@mytreatplan.com gets the alert. The next nightly run turns the monitor ok again.
9. **First restore test**: follow "Restore test" under
   [Backups and restore](#backups-and-restore) and record it.

### 11. KMS (OVHcloud Key Management Service)

Field encryption (signup, story 2.12) wraps its data keys with a **service key** in an
OVHcloud KMS domain in France. This step creates the domain and the staging key, gives
only this server an access certificate for it, and proves the server can use it with
`manage.py kmscheck`. See [Keys (KMS)](#keys-kms) for how it works, renewal and rotation.

1. **KMS domain.** OVHcloud Control Panel → *Identity, Security & Operations* → *Key
   Management Service* → order a KMS in region **GRA** (`eu-west-gra`, Gravelines, the
   same region as the server and database). On the domain's *General information* tab,
   copy the **domain id** (`okmsId`, a UUID), check the **REST endpoint** is
   `https://eu-west-gra.okms.ovh.net`, and copy the domain's **URN** (for the policy in
   step 4).
2. **Staging service key.** In the domain → *Encryption keys* → generate a key: name
   `mytp-staging`, type **`oct`** (symmetric), size **256**, operations **wrapKey**,
   **unwrapKey**, **encrypt** and **decrypt**, protection level **`SOFTWARE`**
   (production, story 1.12, may choose `HSM`). Creating and unwrapping data keys needs
   `wrapKey`/`unwrapKey`: a key with only `encrypt`/`decrypt` makes `kmscheck` fail at
   `generate` with HTTP 400, and a key's operations cannot be changed later (replace the
   key while nothing uses it). `encrypt`/`decrypt` are for re-wrapping during rotation. Copy its **key id** (a UUID). Never delete or deactivate it
   from here on without following "Rotate the service key".
3. **Server identity and access certificate.** IAM is at account level, not in the
   Public Cloud project (its *Users & Roles* is something else): account menu (top
   right) → *Identity and Access Management (IAM)* → *Identities* (or *My account* →
   *Users management*) → add a local user `mytp-kms-staging` (no console login needed; it is
   only the certificate's identity). Then create the private key and a CSR **on the
   server**, so the key is born there:

   ```sh
   sudo -u mytp-dev sh -c 'umask 077 && openssl req -new -newkey rsa:3072 -nodes \
       -subj "/CN=mytp-kms-staging" \
       -keyout /srv/mytp-dev/shared/kms-client.key -out /srv/mytp-dev/shared/kms-client.csr'
   sudo cat /srv/mytp-dev/shared/kms-client.csr
   ```

   In the KMS domain → *Generate an access certificate*: validity **365 days** (or the
   maximum offered), paste the CSR, and link **only** the identity `mytp-kms-staging`
   (never the account's root identity: it would bypass the IAM policy). Download the
   certificate (PEM) and copy it to the server:

   ```sh
   scp kms-client.crt debian@<instance-ip>:/tmp/kms-client.crt
   ssh debian@<instance-ip> 'sudo install -m 600 -o mytp-dev -g mytp-dev /tmp/kms-client.crt /srv/mytp-dev/shared/kms-client.crt && rm /tmp/kms-client.crt && sudo rm /srv/mytp-dev/shared/kms-client.csr'
   ```

   **No backup copy of the private key:** it is created on the server and never leaves
   it (not to a laptop, the password manager, chats or tickets). If it is lost, for
   example when the server is rebuilt, issue a new certificate ("Renew the access
   certificate" below); the data stays readable, because what protects it is the service
   key inside the KMS. In the team password manager, keep only a reference entry
   "MyTreatplan staging KMS" with the expiry date, domain id, endpoint and key id (none
   of them secret). Delete any local copy of the certificate. The credential files live
   only in `/srv/mytp-dev/shared/` (owner `mytp-dev`, mode 600), never in the repo.
4. **IAM policy.** *Identity, Security & Operations* → *Policies*
   (https://www.ovh.com/manager/#/iam/policy) → *Create a policy*
   `mytp-kms-staging-datakeys`: identity `mytp-kms-staging`; product type **Key
   Management Service** first (the resource list is filtered by it, and a policy without
   a resource grants nothing: the KMS then answers "Domain was not found or no actions
   have been granted for your identity"); resource: **this KMS domain only**
   (`urn:v1:eu:resource:okms:<domain id>`); actions **only**, typed in by hand with
   "Authorise all actions" off:

   - `okms:apikms:serviceKey/get`
   - `okms:apikms:serviceKey/dataKey/create`
   - `okms:apikms:serviceKey/dataKey/decrypt`

   Nothing that creates, updates, imports, activates, deactivates or deletes keys, and
   no `okms:apiovh:*` actions. The server can then make and unwrap data keys but cannot
   manage keys.
5. **Settings.** Fill in the `KMS_*` keys in `/srv/mytp-dev/shared/settings.env` (see
   `deploy/staging.env.example`; no comment after a value):

   ```sh
   sudo -u mytp-dev nano /srv/mytp-dev/shared/settings.env
   # KMS_ENDPOINT=https://eu-west-gra.okms.ovh.net
   # KMS_OKMS_ID=<domain id>
   # KMS_SERVICE_KEY_ID=<key id>
   # KMS_CLIENT_CERT=/srv/mytp-dev/shared/kms-client.crt
   # KMS_CLIENT_KEY=/srv/mytp-dev/shared/kms-client.key
   # KMS_CA_FILE stays empty (system CAs):
   # KMS_CA_FILE=
   sudo ls -l /srv/mytp-dev/shared/kms-client.*     # -rw------- mytp-dev mytp-dev, both files
   ```

6. **Check by hand.**

   ```sh
   cd /srv/mytp-dev/current
   sudo -u mytp-dev ./venv/bin/python manage.py kmscheck
   # kmscheck: client certificate expires 2027-10-.. ..:.. UTC (364 days)
   # kmscheck: service key <key id> state active
   # kmscheck: round trip ok
   ```

   A failure names its step: `settings` (a key is missing or a file unreadable/not
   600), `cert` (expiry unreadable), `connect` (DNS, TLS, timeout), `auth` (the
   certificate or key could not be loaded, the KMS refused the certificate in the TLS
   handshake, or HTTP 401/403: check the identity and the IAM policy),
   `get`/`generate`/`unwrap` (HTTP status, e.g. 404 unknown key, 400 deactivated key),
   `mismatch`.
7. **Daily job.** From a copy of the merged `deploy/`, re-run
   `bash ~/deploy/provision.sh dev dev.mytpdso.com <htpasswd-user>`; it installs
   `mytp-dev-job@kmscheck.timer` (04:00 UTC). Then:

   ```sh
   systemctl list-timers 'mytp-dev-job@*'          # lists kmscheck
   sudo systemctl start mytp-dev-job@kmscheck      # Sentry Crons: mytp-dev-kmscheck ok
   ```

   Set the monitor's **Notify** to devops@mytreatplan.com (step 8.5).

### 12. Field encryption keys (before merging story 2.2)

From story 2.2 the app encrypts personal fields (the users' email and names, the pending
registrations' email) with AES-256-GCM, and finds emails through a blind index
(HMAC-SHA256). It needs two keys in `/srv/mytp-dev/shared/settings.env`:

- `FIELD_ENCRYPTION_KEYS`: `<key_id>:<base64 32-byte key>` entries, comma-separated. The
  first encrypts; the others only decrypt (during a rotation).
- `BLIND_INDEX_KEY`: its own base64 32-byte key.

Do this **before the merge**: the deploy runs `migrate`, which refuses to run without
valid keys (check errors `mytp_core.E001` / `mytp_core.E002`, naming the setting), and
whose data migration encrypts the existing users and pending registrations in place. A
deploy without the keys fails at `migrate` and the previous release stays live.

**Steps 1–2 below are the plain-key setup: staging before story 2.12, and the plain keys'
transition role during "Move to the KMS provider" only.** They are not the normal setup
any more. A new environment (production, story 1.12) never gets plain keys: after step 11
it starts directly with KMS keys, generated with `manage.py generate_kms_field_key` as in
"Move to the KMS provider" step 1 (with nothing to re-encrypt, steps 2, 4 and 5 there do
not apply).

1. **Generate the keys on the server**, straight into the settings file, so they never
   show on a screen, a laptop or a chat (the release before 2.2 has no
   `generate_field_keys` yet; this is the same thing):

   ```sh
   sudo -u mytp-dev sh -c 'umask 077
   k() { python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"; }
   printf "FIELD_ENCRYPTION_KEYS=k%s:%s\nBLIND_INDEX_KEY=%s\n" "$(date -u +%Y%m%dT%H%M)" "$(k)" "$(k)" \
       >> /srv/mytp-dev/shared/settings.env'
   sudo grep -c -E '^(FIELD_ENCRYPTION_KEYS|BLIND_INDEX_KEY)=.' /srv/mytp-dev/shared/settings.env   # 2
   sudo ls -l /srv/mytp-dev/shared/settings.env                                                    # -rw------- mytp-dev mytp-dev
   ```

   If the file already has empty `FIELD_ENCRYPTION_KEYS=` / `BLIND_INDEX_KEY=` lines, delete
   them (`sudo -u mytp-dev nano /srv/mytp-dev/shared/settings.env`). Once 2.2 is live,
   `manage.py generate_field_keys` prints new keys the same way (to the terminal only).
2. **Copy them to the team password manager**, entry **"MyTreatplan staging field keys"**:
   open the file with `sudo -u mytp-dev nano /srv/mytp-dev/shared/settings.env`, copy the two
   lines into the entry, add the date and the key id (`k<UTC date>T<time>`, e.g. `k20261013T0812`), close without saving.
   Nowhere else: not the repo, a ticket, a chat, Sentry or a local file. Record that it
   was done in the pull request ("field keys in the password manager, <date>, <who>").
3. **Merge**, let the deploy run, then check:

   ```sh
   cd /srv/mytp-dev/current
   sudo -u mytp-dev ./venv/bin/python manage.py check                 # no issues
   sudo -u mytp-dev ./venv/bin/python manage.py shell -c "from accounts.models import User; print(User.objects.by_email('<staging user email>').exists())"   # True
   ```

**Rolling back past 2.2 needs the migrations reversed first.** Once `migrate` has run
accounts 0003–0005 and signup 0002–0004, a pre-2.2 release reads ciphertext as emails and
names and cannot create users (it does not set `email_index`). This includes
`release.sh`'s automatic rollback after a failed health check. To go back, keep the keys
set and, **from the 2.2 release** (it has the reverse migrations), decrypt first, then
switch:

```sh
cd /srv/mytp-dev/releases/<2.2 sha>
sudo -u mytp-dev ./venv/bin/python manage.py migrate accounts 0002
sudo -u mytp-dev ./venv/bin/python manage.py migrate signup 0001
# then point `current` at the older release and restart (see "Rollback")
```

If an automatic rollback already switched back, run the same two commands from the 2.2
release directory right away.

**Losing a key makes the data unreadable.** AES-256-GCM has no back door: without the
`FIELD_ENCRYPTION_KEYS` entry a value was encrypted with, nobody (us included) can read
that value again, and a lost key cannot be reissued. Without `BLIND_INDEX_KEY` no one can
log in or be found by email until every index is recomputed (which needs the encryption
key). With the plain keys, the password manager copy is their only backup; with the KMS
provider (below) what protects the data is the KMS service key ("Keys (KMS)",
"Disaster recovery").

**Backups need the same keys.** The nightly `backupdb` dumps (and OVHcloud's managed
backups) hold the ciphertext, not the plaintext. A restored database is readable only with
the `FIELD_ENCRYPTION_KEYS` entries its values were encrypted with, and logins work only
with the same `BLIND_INDEX_KEY`. So a key stays in the password manager for as long as any
backup encrypted with it may be restored (30 days of dumps after a rotation), and a
restore needs the keys as they were when the dump was taken.

**Rotate the encryption key** (e.g. yearly, or at once if a key may have leaked):

1. `cd /srv/mytp-dev/current && sudo -u mytp-dev ./venv/bin/python manage.py generate_field_keys --rotate`
   prints one new entry. Put it **first** in `FIELD_ENCRYPTION_KEYS`, keeping the current
   entries after it (comma-separated, no spaces), and add it to the password manager entry.
2. `sudo -u mytp-dev ./venv/bin/python manage.py check` (no issues: the edited list parses),
   then `sudo systemctl restart mytp-dev-gunicorn` (new values now use the new key; old ones
   still decrypt). A bad key would stop Gunicorn from starting.
3. `sudo -u mytp-dev ./venv/bin/python manage.py reencrypt_fields` rewrites every encrypted
   value with the new key (and recomputes the blind indexes). It prints row counts and key
   ids only, and fails if any value is still on an older key.
4. Remove the old entry from `settings.env` only once no backup encrypted with it can
   still be needed (30 days); keep it in the password manager until then.

`BLIND_INDEX_KEY` is not rotated routinely. If it must change (leak), change it and run
`reencrypt_fields` right after the restart: until it finishes, email lookups and logins
fail.

The rotation above is for the plain keys (development, or staging before the move
below). With the KMS provider, see "Keys (KMS)", "Rotate a field key".

#### Move to the KMS provider (story 2.12)

From story 2.12 staging keeps no plain field key: `settings.env` holds only data keys
wrapped by the KMS service key (step 11), and the app unwraps them through the KMS once
per process, keeping the plain keys in memory only. `check --deploy` fails
(`mytp_publicsite.E004`) while `DEBUG` is off and `FIELD_KEY_PROVIDER` is not
`accounts.crypto.KMSKeyProvider`.

- `FIELD_KMS_KEYS`: `<key_id>:<wrapped key>` entries, comma-separated. The first encrypts;
  the others only decrypt (during a rotation).
- `BLIND_INDEX_KMS_KEY`: the blind index key, one wrapped value. It replaces
  `BLIND_INDEX_KEY`.
- With the KMS provider, `FIELD_ENCRYPTION_KEYS` may still hold the plain keys during the
  move, **for decryption only**: a KMS key is always current. `BLIND_INDEX_KEY` is ignored.

`check` (and so `migrate`) only parses these settings, without calling the KMS; the
daily `kmscheck` job unwraps every one of them and fails if one cannot be unwrapped. A KMS
failure when the app first needs a key raises an error naming the step and the key id
(`FIELD_KMS_KEYS: cannot unwrap key 'kms...' through the KMS: step connect failed: ...`);
it is not kept, so the next request retries.

The staging move is a rotation (decision 2026-10-14). Run every command yourself, in your
own terminal on the server, once the 2.12 release is live and step 11's `kmscheck` passes:

1. **Generate the KMS keys** straight into the settings file (the wrapped keys are useless
   without the KMS service key, but they still belong only in `settings.env`). The command
   asks the KMS for a new 256-bit data key, prints only the wrapped key and discards the
   plain one:

   ```sh
   cd /srv/mytp-dev/current
   sudo -u mytp-dev sh -ec 'umask 077
   f=$(./venv/bin/python manage.py generate_kms_field_key "kms$(date -u +%Y%m%dT%H%M)")
   b=$(./venv/bin/python manage.py generate_kms_field_key --blind-index)
   printf "FIELD_KEY_PROVIDER=accounts.crypto.KMSKeyProvider\nFIELD_KMS_KEYS=%s\nBLIND_INDEX_KMS_KEY=%s\n" "$f" "$b" \
       >> /srv/mytp-dev/shared/settings.env'
   sudo grep -c -E '^(FIELD_KEY_PROVIDER|FIELD_KMS_KEYS|BLIND_INDEX_KMS_KEY)=.' /srv/mytp-dev/shared/settings.env   # 3
   ```

   If the file already had any of these three lines, delete the older ones
   (`sudo -u mytp-dev nano /srv/mytp-dev/shared/settings.env`).
2. **Keep the plain keys as transition keys**: leave `FIELD_ENCRYPTION_KEYS` as it is
   (existing values still decrypt with it) and `BLIND_INDEX_KEY` until step 5 (ignored by
   the KMS provider).
3. **Turn signups off, check and restart.** Between this restart and the end of step 4
   email and phone lookups miss (the blind indexes still use the old key), so a signup
   with an already registered email would get through and create a second user. Its new
   index then collides with the old row's recomputed one (`email_index` is unique) and
   `reencrypt_fields` aborts. Set `PORTAL_ENABLED=False` in `settings.env` first:

   ```sh
   sudo -u mytp-dev nano /srv/mytp-dev/shared/settings.env           # PORTAL_ENABLED=False
   sudo -u mytp-dev ./venv/bin/python manage.py check            # no issues (parses only)
   sudo -u mytp-dev ./venv/bin/python manage.py kmscheck         # ... field key kms... unwrap ok
                                                             # ... blind index key blind-index unwrap ok
   sudo systemctl restart mytp-dev-gunicorn
   ```

   New values are now encrypted with the KMS key. **Email and phone lookups (login, the
   signup duplicate checks, the admin search by email) fail from this restart until
   step 4 finishes**, because the blind indexes still use the old key. That is accepted on
   staging; do steps 3 and 4 back to back.
4. **Re-encrypt:**

   ```sh
   sudo -u mytp-dev ./venv/bin/python manage.py reencrypt_fields   # ... done, N rows on key kms...
   ```

   It rewrites every value with the KMS key and recomputes every blind index with the KMS
   blind index key, and fails if any value is still on an older key. Once it reports
   every value on the new key, set `PORTAL_ENABLED=True` again in `settings.env` and
   `sudo systemctl restart mytp-dev-gunicorn`. Then log in to staging and open a user in the
   admin.

   **If it aborts with an `IntegrityError` on `email_index`** (a duplicate user was created
   in the window anyway), find the newer user by its new index and delete it, then run
   `reencrypt_fields` again (it is safe to repeat):

   ```sh
   sudo -u mytp-dev ./venv/bin/python manage.py shell -c "from accounts.crypto import email_index; from accounts.models import User; print(list(User.objects.filter(email_index=email_index('<the email>')).values_list('pk', 'date_joined')))"
   # the one listed is the duplicate (the old row still has its old index); delete it:
   sudo -u mytp-dev ./venv/bin/python manage.py shell -c "from accounts.models import User; User.objects.get(pk='<its pk>').delete()"
   ```

   The error does not name the email; the duplicate is the user who signed up during the
   window (newest `date_joined` in the admin).
5. **Retire the plain keys.** Delete the `FIELD_ENCRYPTION_KEYS` and `BLIND_INDEX_KEY`
   lines from `settings.env`, then:

   ```sh
   sudo -u mytp-dev ./venv/bin/python manage.py check                # no issues
   sudo -u mytp-dev ./venv/bin/python manage.py check --deploy       # no mytp_publicsite.E004
   sudo systemctl restart mytp-dev-gunicorn
   sudo grep -c -E '^(FIELD_ENCRYPTION_KEYS|BLIND_INDEX_KEY)=.' /srv/mytp-dev/shared/settings.env   # 0
   ```

   Add the key ids and the wrapped values of `FIELD_KMS_KEYS` and `BLIND_INDEX_KMS_KEY`
   (wrapped only, never a plain key) to the reference entry "MyTreatplan staging KMS".
   Keep the entry "MyTreatplan staging field keys" (the plain keys) in the team password
   manager for 30 days, until the last `backupdb` dump taken with them expires, then delete
   it; note the deletion date in it now. Record it in the pull request ("plain field keys
   retired from settings.env, <date>, <who>; password manager entry to delete on <date>").
   Until then, a dump taken before step 4 is restored with those plain keys as
   `FIELD_ENCRYPTION_KEYS` (transition keys) and `BLIND_INDEX_KEY`; afterwards it can no
   longer be read. A release older than 2.12 can no longer start (it needs the plain keys
   in `settings.env`).

Production (story 1.12) follows the same steps with its own KMS domain, service key and
certificate.

## How a deploy works

1. CI (`test`) passes on a push to `master`. Pull requests and other branches never deploy.
2. `deploy-staging` builds `release.tar.gz` (`git archive` of the commit plus the built
   `static/css/site.css` and, last, the marker `deploy/release-ready`; both are
   git-ignored). If `master` has moved on to a newer commit by then, the job
   ends green without deploying (the newer push deploys itself). Otherwise it copies the
   tarball to `/srv/mytp-dev/incoming/` and runs, as `mytp-dev`,
   `/usr/local/bin/mytp-release dev <sha> <tarball>` (the host's copy of `release.sh`,
   installed by `provision.sh`; a change to `release.sh` reaches the host when
   `provision.sh` is run again from the merged `deploy/`).
3. The release command refuses a site that was never provisioned (no `/srv/mytp-<site>`),
   another user than `mytp-<site>`, or a `settings.env` whose `SITE` differs. It unpacks
   to `releases/<sha>/`, refuses a tarball without `manage.py` or `deploy/release-ready`
   (so any site repo's CI build deploys, not only the portal's), links `settings.env`, builds the
   venv, runs `compilemessages`, `collectstatic`, stops the task worker
   (`sudo systemctl stop mytp-dev-worker`), runs `migrate`, `createcachetable`.
   If any step fails, the half-built release is removed, `current` is unchanged, the
   worker is started again and the job is red with that step's output.
4. It switches `current`, runs `sudo systemctl restart mytp-dev-gunicorn` and requests
   `/robots.txt` through the socket, then starts the worker
   (`sudo systemctl restart mytp-dev-worker`) and checks it is still running 3 seconds
   later; these are the only sudo rights `mytp-dev` has. The worker is stopped from
   `migrate` to here, so it never runs the old code on the new schema. If either check
   fails, it switches back to the previous release, restarts both, and the job is red. A
   site whose worker unit is not enabled (not yet provisioned with it) skips the worker.
5. It keeps the newest 5 releases (plus the live and previous one).

Deploys of a site run one at a time (concurrency group `deploy-staging-<site>`, one per
matrix leg; the release command also holds a lock per site). dev and dev-ae deploy in
parallel, each from its own leg: one failing never cancels or rolls back the other. The
dev-ae leg fails until the [Add dev-ae](#add-dev-ae) runbook is done. If several pushes
queue up, GitHub keeps only the newest pending one, and a job whose commit is no longer
the tip of `master` skips the deploy, so the latest commit is what goes live even when an
older push's tests finish last. Each leg checks the tip on its own: if `master` moves
between the two checks, one site may skip a commit the other released; the next push
brings both to the same commit.

Note: migrations run before the switch, so the previous release briefly runs against the
new schema, and a failed health check does not undo migrations. Keep migrations
backwards-compatible.

## Scheduled jobs

Jobs are listed in `deploy/jobs.conf`, one per line: `name | manage.py command | schedule`.
The schedule is a systemd `OnCalendar` in UTC, e.g. `*-*-* 03:30:00 UTC` (daily) or
`Mon *-*-* 02:00 UTC` (weekly); `mytp_core/jobs.py` (mytp-core) lists the forms supported
(they must also map to the Sentry monitor's crontab, and the tests check every line).

`provision.sh` installs the timers and a copy of the `jobs.conf` it used at
`/srv/mytp-dev/shared/jobs.conf`. `runjob` reads the job list and schedules from that copy
(falling back to the release's own file only when it is missing, e.g. locally), so the
Sentry monitors always match the timers. A deploy that changes `jobs.conf` does not change
the timers: `release.sh` prints a warning, and you must re-run `provision.sh`.

Each job runs as `mytp-dev` in `/srv/mytp-dev/current` via `deploy/run-job.sh dev <name>` →
`manage.py runjob <name>` (from mytp-core), which sends Sentry cron check-ins (in progress,
then ok or error) to the monitor `mytp-<site>-<name>`, e.g. `mytp-dev-clearsessions`. The
site name (`SITE`), not the Sentry environment, tells the monitors apart, since several
sites on one host share the environment `staging`. A missed run (host down, timer gone) shows as a missed check-in in Sentry; a
failed run exits non-zero, so the unit is marked failed. Timers are `Persistent=true`:
a run missed while the host was down happens at boot.

To add a job:

1. Add a line to `jobs.conf` (the command must exist in the app) and merge it.
2. On the server, from a copy of the merged `deploy/`, run `bash ~/deploy/provision.sh
   dev dev.mytpdso.com <htpasswd-user>`. It installs and starts the new timer, updates
   changed schedules and removes this site's timers no longer listed (never another
   site's).
3. After its first run, set the Sentry monitor's notifications (step 8.5).

To remove a job: delete its line, merge, re-run `provision.sh` (it stops and removes the
timer), then in Sentry → Crons delete (or disable) the monitor `mytp-<site>-<name>`;
otherwise it keeps raising missed check-in alerts.

**One-off, after the merge of epic-platform entry 5 (mytp-core 0.2.0).** The monitors were
named after the Sentry environment (`mytp-staging-<name>`); they are now named after the
site. Nothing needs re-provisioning (`release.sh` is unchanged):

1. After the deploy, run one job by hand and check its check-in lands on the new monitor,
   and that `/healthz` names the site:

   ```sh
   sudo systemctl start mytp-dev-job@clearsessions   # Sentry Crons: mytp-dev-clearsessions ok
   curl -s https://dev.mytpdso.com/healthz           # ok / site: dev / region: eu
   ```

2. Each new `mytp-dev-<name>` monitor appears at its job's first run; set its **Notify**
   to devops@mytreatplan.com (step 8.5).
3. In Sentry → Crons, delete the six old monitors `mytp-staging-clearsessions`,
   `mytp-staging-purgepending`, `mytp-staging-eventlogretention`, `mytp-staging-backupdb`,
   `mytp-staging-kmscheck` and `mytp-staging-purgeerased`; otherwise they raise missed
   check-in alerts.

Run a job by hand / read its logs:

```sh
systemctl list-timers 'mytp-dev-job@*'             # next and last run of each job
sudo systemctl start mytp-dev-job@clearsessions    # run now (waits until it finishes)
systemctl status mytp-dev-job@clearsessions        # last result
sudo journalctl -u mytp-dev-job@clearsessions -n 100
systemctl list-units --failed 'mytp-dev-job@*'
```

## Geo-IP database

The region suggestion (epic-regions entry 5) needs the visitor's country. `regions.geoip`
looks the client IP up on the instance, in the DB-IP Lite country database (MMDB, CC BY
4.0) at `GEOIP_DB_PATH` (default `/srv/mytp-<site>/shared/geoip/dbip-country-lite.mmdb`);
no IP leaves the server. Until the file exists every lookup answers "unknown" (None).

The `geoipupdate` job (`manage.py update_geoip`, 04:45 UTC on the 3rd of each month)
downloads `https://download.db-ip.com/free/dbip-country-lite-YYYY-MM.mmdb.gz` for the
current month (the previous one while it is not out yet), checks it is a DB-IP country
database that answers 8.8.8.8 as US and 2001:4860:4860::8888 as some country (DB-IP
says CA), and only then swaps it in
with an atomic rename; the app picks up the new file without a restart. Any failure keeps
the installed file, logs an error to Sentry and fails the job. The job prints only the
month, the size and the result. Its Sentry monitor `mytp-<site>-geoipupdate` stays
disabled for now (the Developer-plan gap, epic-platform Notes): the first run creates it
enabled, so install step 3 disables it.

Staging only: with `GEOIP_OVERRIDE_HEADER_ENABLED=True` (`deploy/staging.env.example`) a
request header `X-MyTP-Geo-Country: SA` fakes the visitor's country. `check` fails
(`regions.E001`) when it is on while `ROBOTS_NOINDEX` is off, so it cannot reach a public
site.

**Install (each site, once; after the merge of epic-regions entry 4):**

1. From a copy of the merged `deploy/`, re-run `provision.sh` for the site (it creates
   `shared/geoip` and installs the `geoipupdate` timer; see [Scheduled jobs](#scheduled-jobs)).
   On dev and dev-ae, add `GEOIP_OVERRIDE_HEADER_ENABLED=True` to `shared/settings.env`
   and restart the app (`sudo systemctl restart mytp-dev-gunicorn`).
2. Run the job once (it waits until it finishes):

   ```sh
   systemctl list-timers 'mytp-dev-job@*'           # lists geoipupdate
   sudo systemctl start mytp-dev-job@geoipupdate    # or: sudo -u mytp-dev bash /srv/mytp-dev/current/deploy/run-job.sh dev geoipupdate
   sudo journalctl -u mytp-dev-job@geoipupdate -n 20  # "update_geoip: installed DB-IP country lite YYYY-MM (... MB)"
   sudo ls -l /srv/mytp-dev/shared/geoip/            # dbip-country-lite.mmdb, owned by mytp-dev
   ```

3. In Sentry → Crons, disable the monitor `mytp-<site>-geoipupdate` (e.g.
   `mytp-dev-geoipupdate`) that this first run created, for each site: the Developer plan
   has a single cron monitor (accepted gap, epic-platform Notes).
4. Check in the shell (in `/srv/mytp-dev/current`):

   ```sh
   sudo -u mytp-dev ./venv/bin/python manage.py shell -c "
   from django.test import RequestFactory
   from regions.geoip import request_country
   rf = RequestFactory()
   print(request_country(rf.get('/', HTTP_X_MYTP_GEO_COUNTRY='SA')))  # SA (override on)
   print(request_country(rf.get('/', REMOTE_ADDR='88.0.0.1')))        # ES (a Spanish IP, Telefónica)
   print(request_country(rf.get('/', REMOTE_ADDR='10.0.0.1')))        # None (private)"
   ```

A failed run (e.g. no network) leaves `dbip-country-lite.mmdb` unchanged (same size and
time in `ls -l`) and no `.tmp` file in `shared/geoip`.

## Background tasks

On-demand work (outbox delivery, emails and SMS, webhooks, scans, timers on records) runs
as Django `django.tasks` tasks (architecture AD-8), queued in the site's own database
(django-tasks-db) and run by **one worker per site**: `mytp-dev-worker.service`, which
runs `manage.py db_worker` in `/srv/mytp-dev/current` as `mytp-dev` with the site's
`settings.env`. No Redis, no Celery. For now only a sample task and the heartbeat exist.

- **Start, stop, restart.** `provision.sh` installs, enables and starts the unit
  (`deploy/systemd/worker.service.template`) once the live release ships the worker
  (django-tasks-db in its `requirements.txt`). Every release stops it before `migrate`
  and starts it after the switch (`release.sh`); a release whose worker cannot start or
  exits within 3 seconds is rolled back. systemd restarts a worker that dies
  (`Restart=always`, 5 s apart) but gives up after 10 starts in 5 minutes (the unit is
  then `failed` and the heartbeat alerts; fix the cause, then
  `sudo systemctl restart mytp-dev-worker`). It runs the tasks of every queue.
  The worker reads `settings.env` at start: after editing it, restart both,
  `sudo systemctl restart mytp-dev-gunicorn mytp-dev-worker`.
- **Interrupted tasks.** Before each start, `manage.py requeue_interrupted_tasks`
  (ExecStartPre) puts back in the queue any task still marked running: only this worker
  runs the site's tasks, so such a task was interrupted (the worker was killed). Its
  effects are idempotent, so none happens twice. Never run `manage.py db_worker` (or
  that command) by hand while the unit runs.
- **Idempotency.** Every task is keyed by its record's UUID (and by period for periodic
  effects such as renewals) and wraps each effect in `mytp_core.tasks.run_once`, which
  records it in the `mytp_core_taskeffect` table in the same transaction. Code enqueues a
  task with `mytp_core.tasks.enqueue_on_commit`, so a rolled-back transaction enqueues
  nothing. Retries that matter (outbox, webhooks, messages) are tracked on those business
  rows; a failed task is not retried by the worker, and its ERROR reaches Sentry.
- **Heartbeat.** The `workerheartbeat` job (every 5 minutes, `jobs.conf`) enqueues a
  task; when the worker runs it, it checks in on the Sentry cron monitor
  **`mytp-dev-worker`** (schedule `*/5 * * * *`, 3 minutes' margin). If the worker is down
  or stuck, check-ins stop and Sentry raises a missed check-in alert. A heartbeat that
  waited in the queue over 5 minutes is skipped, so a worker coming back does not report
  its backlog as alive. The job itself has its own monitor (`mytp-dev-workerheartbeat`),
  which tells a stopped timer apart from a stopped worker.
- **Pruning.** The `prunetasks` job (daily at 04:30 UTC) deletes task results finished
  more than 14 days ago (failed ones after 30 days); tasks waiting or running are kept.

Check the worker and read its logs (task ids, task paths and record UUIDs only; never
personal data):

```sh
systemctl status mytp-dev-worker
sudo journalctl -u mytp-dev-worker -n 100
cd /srv/mytp-dev/current
sudo -u mytp-dev ./venv/bin/python manage.py enqueue_sample_task   # prints the record UUID
sudo journalctl -u mytp-dev-worker -n 20        # "sample task effect for <uuid>", SUCCESSFUL
```

Enqueue it again with `--record <that uuid>`: the worker runs the task again but logs
`effect sample.logged for <uuid> already done; skipped`.

Check that a killed worker comes back and nothing runs twice:

```sh
sudo systemctl kill mytp-dev-worker              # SIGTERM: finishes its task, exits
sleep 7; systemctl is-active mytp-dev-worker     # active (systemd restarted it)
sudo systemctl kill -s KILL mytp-dev-worker      # killed outright, mid-task if busy
sleep 7; sudo journalctl -u mytp-dev-worker -n 20   # "requeued N interrupted task(s)"
```

**One-off, after the merge of epic-platform entry 9 (mytp-core 0.3.0).** The release
command, the sudoers rule, the worker unit and two job timers are new, so `provision.sh`
must run again, **after** the merge's deploy has gone live: the worker unit is installed
only once the live release ships the worker. Run before it, provision installs the rest
but no worker (and the two new jobs fail until the release is live), so run it again
after the deploy. On the server as `debian`, from a copy of the merged `deploy/`:

1. `bash ~/deploy/provision.sh dev dev.mytpdso.com <htpasswd-user>`: it reports the new
   sudoers file, `mytp-dev-worker.service` installed, enabled and started, the
   `workerheartbeat` and `prunetasks` timers and `/usr/local/bin/mytp-release`.
2. `systemctl status mytp-dev-worker` is active; run the sample task check above.
3. Within 10 minutes, Sentry → Crons shows `mytp-dev-worker` and
   `mytp-dev-workerheartbeat` (and `mytp-dev-prunetasks` after its first run, or
   `sudo systemctl start mytp-dev-job@prunetasks`); set each one's **Notify** to
   devops@mytreatplan.com (step 8.5).
4. Check the alert: `sudo systemctl stop mytp-dev-worker`, wait about 10 minutes for the
   missed check-in alert on `mytp-dev-worker`, then `sudo systemctl start mytp-dev-worker`
   (the next heartbeat turns the monitor ok again).

## Twilio Verify

Signup codes (the email code, the mobile code and the resume code) and the login code (the
second factor) are sent and checked by Twilio Verify (`signup/twilio_verify.py`). Twilio
generates, stores, expires and counts the codes: 6 digits, valid 10 minutes, invalid after
5 wrong attempts. Email goes through Verify's email channel (SendGrid), mobile through
WhatsApp, else SMS. One Verify service serves every purpose (signup, resume and login): a
login code and a pending signup or resume code to the same contact share Twilio's pending
code and its 5-attempt count (accepted: each proves control of the same contact). Our own
cooldown and hourly limits are kept per purpose. Until it is set up, staging stays on the fake
provider, which writes each code to the journal; `check --deploy` fails while the fake is
selected (`mytp_publicsite.E006`).

**Order** (not everything on Twilio's side is ready at once):

1. Create the Verify service and the SendGrid email integration (console steps below).
2. Type the credentials into `settings.env` and switch `VERIFICATION_PROVIDER` (below).
   `TWILIO_WHATSAPP_ENABLED` stays `False`: mobile codes go by SMS.
3. Once Meta approves the WhatsApp sender, set `TWILIO_WHATSAPP_ENABLED=True` and restart.

### Twilio console

In the Twilio console (account owned by the team, 2FA on):

1. **Verify → Services → Create**: friendly name `MyTreatPlan` (it appears in the SMS
   text). Settings: **code length 6**, **Fraud Guard on**, default TTL (10 minutes). Copy
   the service SID (`VA...`).
   The SMS sender is Twilio's generic pool (shown as `TWVerify` or `AUTHMSG`) unless a
   branded sender ID is requested from Twilio and set as the default. Even then, Twilio
   uses it on a best-effort basis only in countries that don't require sender ID
   pre-registration, and falls back to the generic pool elsewhere.
2. **Trust Hub → Customer Profiles → Primary Customer Profile**: submit the company's
   legal name, registration number, address, website and an authorised representative,
   exactly as in the company register. Until Twilio approves it (usually 1 to 3 business
   days), a paid account still sends SMS only to the account's **Verified Caller IDs**,
   and other numbers fail with Twilio code `21608` (HTTP 403). To test before approval,
   add the test mobile under Phone Numbers → Manage → Verified Caller IDs. Verify it **by
   voice call**: Twilio blocks verification by SMS in some countries, Spain among them.
3. **Messaging → Settings → Geo permissions** (SMS): allow only the countries signup
   accepts numbers from; everything else blocked.
4. **Email integration**: in SendGrid, verify the sender domain and create one **dynamic
   template** that switches on `{{locale}}` (`en`, `es`), showing the code
   (`{{twilio_code}}`) in English or Spanish. In Twilio, Verify → Email Integration →
   Create: the SendGrid API key (restricted to Mail Send), the template id, the from
   address and name. Then attach the integration to the service (Service → Email).
   The template id lives in the console only, not in our settings.
5. **WhatsApp**: Verify → Service → WhatsApp: connect the approved WhatsApp sender. While
   it is pending, leave `TWILIO_WHATSAPP_ENABLED=False`.
6. **API key**: Account → API keys & tokens → Create API key, type **Standard**, name
   `mytp-staging-verify`. The secret is shown once.

### Credentials on the server

Type them by hand straight into the server's settings file; never paste them anywhere
else (chat, tickets, commits, tests). A lost API secret is replaced by a new API key.

```sh
sudo -u mytp-dev nano /srv/mytp-dev/shared/settings.env
#   VERIFICATION_PROVIDER=signup.twilio_verify.TwilioVerifyProvider
#   TWILIO_ACCOUNT_SID=AC...
#   TWILIO_API_KEY_SID=SK...
#   TWILIO_API_KEY_SECRET=...
#   TWILIO_VERIFY_SERVICE_SID=VA...
#   TWILIO_WHATSAPP_ENABLED=False
cd /srv/mytp-dev/current
sudo -u mytp-dev ./venv/bin/python manage.py check            # no signup.E001
sudo -u mytp-dev ./venv/bin/python manage.py check --deploy   # no mytp_publicsite.E006
sudo systemctl restart mytp-dev-gunicorn
```

With the Twilio provider selected and any of the four empty, every `check` and `migrate`
(and so the next deploy) fails with `signup.E001`, naming the setting.

### Sentry alert on failed sends

Every failed send logs the error `Signup code send failed` (channel, purpose, and
Twilio's error code and HTTP status only); a failed check logs `Signup code check failed`.
In Sentry, create a **metric monitor**, not an issue alert. All failed sends group into
one issue (one message template), and an issue alert only runs when an issue changes
state (created, resolved, escalating, regressed). Further failures on an existing issue
would never trigger it. A metric monitor counts the errors continuously:

- Monitors → Create → **Metric** (Number of Errors), project of the portal, environment
  `staging`.
- Filter: `message:"Signup code send failed"`.
- Interval **10 minutes**, Critical threshold **above 4** (5 or more).
- Action: email the member devops@mytreatplan.com (Sentry emails only its members).

Name it "Signup code sends failing".

### Checks on staging

1. Sign up with a real email: the code arrives by email, in English; again with the page
   in Spanish (`/es/`): the email is in Spanish.
2. With WhatsApp off: the mobile code arrives by SMS. With WhatsApp on: by WhatsApp, and
   the page shows "Didn't get it on WhatsApp?" next to "Send by SMS instead"; a number
   without WhatsApp gets it by SMS. In Spanish, the messages are in Spanish.
3. A wrong code shows "This code is not correct"; 5 wrong codes, "no longer valid".
4. The journal has no codes or contacts: `sudo journalctl -u mytp-dev-gunicorn -n 100`.
5. Alert: set `TWILIO_VERIFY_SERVICE_SID` to a wrong `VA...` value, restart, and ask for
   codes 5 times within 10 minutes (resend, other signups): each shows "We couldn't send
   the code"; Sentry has the issue and devops@mytreatplan.com gets the alert. Put the
   right value back and restart.

A wrong `TWILIO_VERIFY_SERVICE_SID` shows first as send failures and the alert: Twilio
answers a code check against a wrong service with the same 404 as "no pending code", so
checks only say "This code is no longer valid" and log no Sentry error.

## Email

The portal's transactional email (the password reset, the contact form) goes over SMTP
through the company's own mail service, sent from `MyTreatPlan <noreply@mytreatplan.com>`
(epic-login entry 9). Signup's verification codes don't: they go through Twilio Verify
("Twilio Verify" above). With `EMAIL_HOST` empty, mail is only written to the journal and
`check --deploy` fails with Django's `mail.E001`.

Type the connection by hand into the server's settings file; the password never goes
anywhere else:

```sh
sudo -u mytp-dev nano /srv/mytp-dev/shared/settings.env
#   EMAIL_HOST=<the mail service's SMTP host>
#   EMAIL_USE_SSL=False            # True for implicit TLS; False means STARTTLS
#   EMAIL_PORT=587                 # optional: 465 with SSL, 587 without, by default
#   EMAIL_HOST_USER=noreply@mytreatplan.com
#   EMAIL_HOST_PASSWORD=<the mailbox password>
cd /srv/mytp-dev/current
sudo -u mytp-dev ./venv/bin/python manage.py check --deploy   # no mail.E001
sudo systemctl restart mytp-dev-gunicorn
sudo -u mytp-dev ./venv/bin/python manage.py sendtestemail <your address>
```

The test email arrives from `noreply@mytreatplan.com`. If it doesn't, the command
prints the SMTP error (authentication, TLS mode or port). Each socket operation waits at
most `EMAIL_TIMEOUT` seconds (default 10). `EMAIL_PORT`, `EMAIL_USE_SSL` and
`EMAIL_TIMEOUT` need a value or no line at all: an empty `EMAIL_PORT=` stops the app
at startup. `DEFAULT_FROM_EMAIL` and `SERVER_EMAIL` default to the noreply address.

**The contact form's inbox.** Each message sent from the home page's contact form is
stored (Admin → Contact messages) and mailed to `CONTACT_INBOX`, from the noreply
address, with Reply-To the sender. It has no default: set it in the same file,

```sh
#   CONTACT_INBOX=webform@mytreatplan.com
```

and restart `mytp-dev-gunicorn`. While it is empty, `check --deploy` fails
(`mytp_publicsite.E007`) and messages are only stored. A message whose mail failed stays in
the admin with its mail "Failed" (filter by mail), and the failure is reported to Sentry
without values.

## Erase an account

A registered user is never deleted in the admin (its legal choices are kept as evidence):
it is **erased**, for example when the user asks (GDPR art. 17).

- **Who:** staff with the `accounts.delete_user` permission (superusers have it). Plain
  delete is off for users.
- **How:** Admin → Users → select the accounts → action **Erase account** → tick "I
  understand this can't be undone" → **Erase**. Your own account and accounts already
  erased are skipped. There is no undo.
- **What happens:** the account is deactivated (no staff or superuser flag, no groups or
  permissions), gets an unusable password (its sessions end), and loses how-found-us and
  the last login. Its email, title, names, country, phone, date joined and legal choices
  are kept as evidence; the commercial consent is recorded as withdrawn. The same email
  (and mobile) can sign up again at once. The admin log records "Erased" with `User <pk>`
  and the staff member, no personal values.
- **Finding it later:** the user list hides erased accounts; set the "status" filter to
  "Erased" (or "All") and search by the exact email.
- **Retention:** `ACCOUNT_ERASURE_RETENTION_DAYS` in `shared/settings.env` (days). It has
  no default, as the lawyer sets it: while it is unset the action refuses, and `check
  --deploy` fails (`mytp_publicsite.E005`). Staging uses `1` until the lawyer gives the
  value. Restart `mytp-dev-gunicorn` after changing it.
- **Purge:** the `purgeerased` job (daily at 04:15 UTC, `manage.py purge_erased_accounts`)
  deletes each erased account with its legal choices once its `purge_after` has passed,
  and prints only the count. On a server provisioned before it, re-run `provision.sh`
  (see [Scheduled jobs](#scheduled-jobs)), then:

  ```sh
  sudo systemctl start mytp-dev-job@purgeerased      # Sentry Crons: mytp-dev-purgeerased ok
  sudo journalctl -u mytp-dev-job@purgeerased -n 20  # the count only
  ```

  and set the monitor's notifications in Sentry → Crons (step 8.5).
- **Backups:** the nightly dumps still hold an erased (or purged) account until they
  expire, 30 days later (see [Backups and restore](#backups-and-restore)).

## Backups and restore

| Layer | Where | Retention | Who restores |
|-------|-------|-----------|--------------|
| OVHcloud managed backups | same provider region as the database (GRA) | 2 days (staging plan, checked 2026-10-12; stored in Gravelines and Strasbourg) | OVHcloud panel (restore or fork the service) |
| Nightly `backupdb` dump | Object Storage **SBG**, `staging/mytreatplan-<UTC>.dump.age` | 30 daily dumps (bucket lifecycle rule) | `deploy/restore-db.sh` |

The managed backups cover everyday mistakes; the SBG dumps cover losing the GRA region or
the managed service, or a managed backup that does not restore.

Both hold personal fields encrypted: a restored database is readable only with the field
keys it was encrypted with (step 12, "Backups need the same keys").

**Nightly job.** `backupdb | backupdb | *-*-* 02:30:00 UTC` in `jobs.conf`, so it runs via
`runjob` with Sentry check-ins (a failed or missed run alerts devops@mytreatplan.com).
`manage.py backupdb`:

1. stops with the missing key's name if any `BACKUP_*` setting is empty, and fails if
   `pg_dump` is older (major version) than the server;
2. runs `pg_dump --format=custom --no-owner --no-privileges` over TLS (`DB_SSLMODE`,
   `DB_SSLROOTCERT`), with the password only in its environment, piped straight into
   `age -r $BACKUP_AGE_RECIPIENT`; only ciphertext is written, in
   `/srv/mytp-dev/shared/backup-tmp` (0700, `mytp-dev`);
3. uploads it as `<BACKUP_PREFIX>mytreatplan-<yyyymmddThhmmssZ>.dump.age`, checks the object
   exists with the same size, and removes the temp file whatever happened.

It never deletes anything; old dumps expire through the lifecycle rule (step 10.3). Its key
cannot delete, but it can still overwrite an existing object (no versioning or object lock
yet; to decide for production, story 1.12). Its
error messages never contain the database password or the S3 keys.

```sh
sudo systemctl start mytp-dev-job@backupdb          # back up now
sudo journalctl -u mytp-dev-job@backupdb -n 50      # last runs
```

### Restore test (quarterly)

Proves the dumps can be restored. On a team Mac, never on the server, into a local,
empty database. Needs `age`, PostgreSQL 17 client tools (`pg_restore`, `psql`), a local
PostgreSQL, and either the `aws` CLI with the S3 keys or a dump downloaded by hand:

```sh
brew install age postgresql@17 awscli
export PATH="$(brew --prefix postgresql@17)/bin:$PATH"   # keg-only formula
brew services start postgresql@17
createdb mytp_restore_test
# The bucket keys from the password manager, in the environment (never as arguments):
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
export BACKUP_S3_ENDPOINT=https://s3.sbg.io.cloud.ovh.net BACKUP_S3_REGION=sbg BACKUP_S3_BUCKET=mytp-backups-staging BACKUP_PREFIX=staging/
# The private key from the password manager, in a temp file only for the run. Never
# inside the repository folder or another synced folder (Google Drive, iCloud):
KEY=$(mktemp) && nano "$KEY"     # paste the whole key entry
bash deploy/restore-db.sh --identity "$KEY" --latest
#   or --object staging/mytreatplan-<...>.dump.age, or --file <downloaded>.dump.age
#   --dry-run runs every check without restoring.
rm "$KEY"; unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
```

The script refuses any target whose name does not end in `_restore_test` (default
`mytp_restore_test`), any target that is not empty, and a non-local `PGHOST`. It decrypts
in a pipe straight into `pg_restore --single-transaction` (no plaintext file), then prints
the number of applied migrations and of users. Compare them with staging:

```sh
cd /srv/mytp-dev/current
sudo -u mytp-dev ./venv/bin/python manage.py shell -c "from django.db.migrations.recorder import MigrationRecorder; from django.contrib.auth import get_user_model; print(MigrationRecorder.Migration.objects.count(), get_user_model().objects.count())"
```

(Anything that changed on staging after the dump was taken shows up as a difference;
run `backupdb` by hand just before the test to compare like with like.) Afterwards:
`dropdb mytp_restore_test`. Record the test below in a pull request.

| Date | Object restored | Migrations / users (dump vs staging) | By |
|------|-----------------|--------------------------------------|----|
| 2026-10-12 | `staging/mytreatplan-20261012T155153Z.dump.age` | 19 / 0 vs 19 / 0 | devops |

To recover for real (region lost): restore the latest dump the same way, by hand,
into the new database service with `age --decrypt -i <key> <dump> | pg_restore --no-owner
--no-privileges -d <new database>`; `restore-db.sh` only restores into `*_restore_test`
on purpose.

## Object storage

Doctors' files (records, STL files, photos; up to 1 GiB each) go from the browser
straight to the region's bucket with presigned S3 multipart (AD-9, the `storage` app).
The site signs every S3 request with its own S3 user's keys (the browser never sees
them), keeps one `StoredObject` per completed upload (the file name field-encrypted) and
deletes through a background task that leaves a `DeletionTrace` (kept for good).

| What | dev |
|------|-----|
| Bucket | `mytp-dev-files`, OVHcloud Object Storage **GRA** (the database's region; never the backup bucket), owner `mytp-backup-admin` |
| Keys | `STORAGE_*` in `shared/settings.env` (`deploy/staging.env.example`): the site's S3 user `mytp-dev-files`, limited to this bucket |
| Object keys | `quarantine/<uuid>` on upload; `clean/<uuid>` once scanned (entry 12). Server-made; never a file name |
| Signed URLs | 15 minutes per upload request; downloads 5 minutes, each recorded first (eventlog kind `file`: object UUID and reason, never the name) |
| Uploads | multipart only, 16 MiB parts; 1 GiB limit per file by default (per purpose) |
| Abandoned uploads | aborted by the bucket's lifecycle rule after `STORAGE_ABORT_INCOMPLETE_DAYS` (7) days |
| Sweep | hourly `sweepstorage` job (`manage.py sweep_storage`, :20 UTC): deletes `quarantine/` objects with no `StoredObject` row older than a day (an upload whose recording never came) or over their purpose's limit, forgets expired pending uploads, and enqueues again deletions not done yet (`delete_requested_at`); prints counts only. Its timer reaches a site when `provision.sh` runs again; on a site without the bucket settings it skips the bucket step |
| Encryption at rest | SSE-S3 (AES256) by default on the bucket |
| Sample page | `/en/storage/sample/`, staff only, with `STORAGE_SAMPLE_UPLOADS=True` (staging only: `check` refuses it without `ROBOTS_NOINDEX`) |
| dev-ae | none yet: its own EU bucket and S3 user come later (`STORAGE_*` stay unset there) |

No key, URL, file name or credential goes into a task argument, a log line or Sentry
(AD-23); the admin site shows no files.

### Bucket and S3 users

The bucket exists (made for the upload spike, entry 2), owned by the admin user
`mytp-backup-admin`, whose keys stay in the team password manager and never go on the
server: only the bucket **owner** can set its lifecycle rule (another user gets
`AccessDenied` even with `s3:*`). For a new region's bucket, do the same: Object Storage →
*Create object container*, S3 API, the region's location, private, linking the admin user
as owner.

The site's S3 user (`mytp-dev-files`; Public Cloud → Users & Roles, role *ObjectStore
operator*, then Object Storage → *S3 users* → generate its credentials) gets only this
policy (import it; replace the bucket name). No bucket-configuration actions: the owner
sets those.

```json
{
  "Statement": [{
    "Sid": "PortalFiles",
    "Effect": "Allow",
    "Action": [
      "s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket",
      "s3:GetBucketLocation", "s3:ListBucketMultipartUploads",
      "s3:ListMultipartUploadParts", "s3:AbortMultipartUpload"
    ],
    "Resource": ["arn:aws:s3:::mytp-dev-files", "arn:aws:s3:::mytp-dev-files/*"]
  }]
}
```

OVHcloud applies a new policy gradually: for several minutes after an import, requests may
get `403 AccessDenied` (the widget retries a refused part once, then the upload fails).
Wait about ten minutes before testing.

### Settings (rename of dev's spike keys)

dev's `settings.env` still has the spike's `UPLOAD_SPIKE_*` lines, which the code no longer
reads. Rename them in place without printing any value, then check the names only:

```sh
sudo -u mytp-dev sed -i \
  -e 's/^UPLOAD_SPIKE_S3_ENDPOINT=/STORAGE_ENDPOINT=/' \
  -e 's/^UPLOAD_SPIKE_S3_REGION=/STORAGE_REGION=/' \
  -e 's/^UPLOAD_SPIKE_S3_BUCKET=/STORAGE_BUCKET=/' \
  -e 's/^UPLOAD_SPIKE_S3_ACCESS_KEY=/STORAGE_ACCESS_KEY=/' \
  -e 's/^UPLOAD_SPIKE_S3_SECRET_KEY=/STORAGE_SECRET_KEY=/' \
  -e 's/^UPLOAD_SPIKE_CORS_ORIGINS=/STORAGE_CORS_ORIGINS=/' \
  -e 's/^UPLOAD_SPIKE_ENABLED=True$/STORAGE_SAMPLE_UPLOADS=True/' \
  /srv/mytp-dev/shared/settings.env
sudo ls -l /srv/mytp-dev/shared/settings.env        # still mytp-dev, -rw-------
sudo grep -oE '^(STORAGE|UPLOAD_SPIKE)_[A-Z_]*=' /srv/mytp-dev/shared/settings.env
#   STORAGE_ENDPOINT= STORAGE_REGION= STORAGE_BUCKET= STORAGE_ACCESS_KEY=
#   STORAGE_SECRET_KEY= STORAGE_CORS_ORIGINS= STORAGE_SAMPLE_UPLOADS=; no UPLOAD_SPIKE_
sudo -u mytp-dev-ae sed -i '/^UPLOAD_SPIKE_/d' /srv/mytp-dev-ae/shared/settings.env   # dev-ae's leftover
cd /srv/mytp-dev/current && sudo -u mytp-dev ./venv/bin/python manage.py check   # no storage.E00x
sudo systemctl restart mytp-dev-gunicorn mytp-dev-worker
```

`STORAGE_ABORT_INCOMPLETE_DAYS` defaults to 7; set it only to change the lifecycle period
(then run `--apply` again).

### CORS, lifecycle and encryption

`manage.py storage_bucket --apply` sets, one at a time, the CORS rule (the origins in
`STORAGE_CORS_ORIGINS`, default `SITE_URL`; methods GET, PUT, POST, DELETE; header
`Content-Type`; `ETag` exposed), the lifecycle rule `abort-incomplete-multipart-uploads`
(abort uploads not finished `STORAGE_ABORT_INCOMPLETE_DAYS` days after they started;
this replaces the spike's 1-day rule) and default encryption SSE-S3, then reads each back.
A refusal prints `set <name>: REFUSED <code> (HTTP <status>): <message>` and the command
exits naming it. Its output never contains the keys.

It needs the bucket owner's keys, passed in the environment for that one command (the
environment overrides `settings.env`). Typed at a prompt (not echoed, not in the shell
history), on the staging host:

```sh
cd /srv/mytp-dev/current
read -rs OWNER_ACCESS_KEY; read -rs OWNER_SECRET_KEY     # paste each, then Enter
sudo -u mytp-dev env STORAGE_ACCESS_KEY="$OWNER_ACCESS_KEY" STORAGE_SECRET_KEY="$OWNER_SECRET_KEY" \
    ./venv/bin/python manage.py storage_bucket --apply
unset OWNER_ACCESS_KEY OWNER_SECRET_KEY
```

(While it runs, the keys are visible in the host's process list to its administrators.)
Read it back at any time with the site's own keys:

```sh
sudo -u mytp-dev ./venv/bin/python manage.py storage_bucket --inspect
#   CORS: 1 rule(s) ... expose ['ETag'] ...; lifecycle: ... abort-incomplete-after 7 day(s);
#   encryption: AES256; incomplete uploads: <n> (each quarantine/<uuid> with its start time)
```

If the site's user may not read the bucket configuration, those three lines show
`AccessDenied`; run `--inspect` the same way as `--apply` instead.

### Staging checks (dev, with the sample page)

Log in at `https://dev.mytpdso.com/admin/` with a staff account, open
`https://dev.mytpdso.com/en/storage/sample/` and copy the page's event log into the
story's results after each test. Test files only, never patient data
(`dd if=/dev/urandom of=~/Desktop/storage-1g.bin bs=1m count=1024; shasum -a 256 ~/Desktop/storage-1g.bin`).

- **T1** Chrome, 1 GB: the log ends with `stored <uuid>: 1073741824 bytes (matches)`.
  Record the measured time and MiB/s (the `uploaded ... in <s> s, <n> MiB/s` line) in the
  story's results, against the spike's 4.7 MiB/s. Uppy 6.0.5 sends one file's 16 MiB parts
  one after another (`limit: 3` is across files; user decision after review).
- **T3** Network drop: turn Wi-Fi off for about 30 s at about 30 %, back on, then press
  Uppy's retry. Record whether it **resumed** (`signed ListParts`, no new
  `signed CreateMultipartUpload`) or restarted, and that it completes.
- **T4** Checksum: Download the T1 (or T3) object from the list within 5 minutes;
  `shasum -a 256` matches the original.
- **T5** Abandoned upload: start a 1 GB upload, close the tab at about 20 %;
  `storage_bucket --inspect` lists it. More than 7 days later (OVHcloud may apply the
  rule a day late): `incomplete uploads: 0`.
- **T7** Gates: `/en/storage/sample/` answers 404 in a private window, for a non-staff
  account, and on dev-ae (setting off).
- **Download expiry:** copy a Download link's target (the redirect) and open it again
  5 minutes and 1 second later: OVHcloud refuses it (`AccessDenied`, request has
  expired). The access record is there:
  `sudo -u mytp-dev ./venv/bin/python manage.py shell -c "from eventlog.models import Event; print(Event.objects.filter(attempt__kind='file').values_list('event', 'reason', 'created_at')[:5])"`.
- **Sweep:** `sudo -u mytp-dev ./venv/bin/python manage.py sweep_storage` prints
  `sweep_storage: <n> pending uploads forgotten, orphan objects <n> deleted, <n> deletions
  enqueued again`; after the merge, run `provision.sh dev` again for its timer.
- **Delete:** Delete an object; with the worker running, it disappears from the list and
  from `--inspect`'s bucket, and its trace is there:
  `... shell -c "from storage.models import DeletionTrace; print(DeletionTrace.objects.values_list('object_id', 'actor_type', 'deleted_at')[:5])"`.

The spike's leftover objects under `spike/` can be deleted in the OVHcloud panel.

## Keys (KMS)

| What | Value |
|------|-------|
| KMS domain | OVHcloud KMS, region **GRA** (`eu-west-gra`), endpoint `https://eu-west-gra.okms.ovh.net`, domain id in `KMS_OKMS_ID` |
| Staging service key | `mytp-staging`: `oct`, 256 bits, wrapKey/unwrapKey/encrypt/decrypt, `SOFTWARE`; id in `KMS_SERVICE_KEY_ID` |
| Server credential | access certificate (mTLS) for IAM identity `mytp-kms-staging`, `/srv/mytp-dev/shared/kms-client.crt` + `.key` (mode 600); expires 2027-10-12 17:07 UTC |
| IAM policy | `mytp-kms-staging-datakeys`: `serviceKey/get`, `serviceKey/dataKey/create`, `serviceKey/dataKey/decrypt` on this domain only (enough for the field key provider, story 2.12: no new action) |
| Field keys | `FIELD_KMS_KEYS`, `BLIND_INDEX_KMS_KEY`: data keys wrapped by the staging service key, in `shared/settings.env` (never in the database); key ids and wrapped values (never plain) in the reference entry, superseded ones kept 30 days |
| Backup of the credential | none (private key never leaves the server); if lost, issue a new certificate. Reference entry (no secrets) "MyTreatplan staging KMS" in the team password manager |
| Production | separate domain/key/certificate, story 1.12 (may use `HSM`) |

**Envelope encryption** (`encryption-policy.md`): the app asks the KMS for a data key
(`POST .../servicekey/<keyId>/datakey`), gets it both plain and wrapped by the service
key, encrypts with the plain one in memory and stores only the wrapped one next to the
data. To read, it sends the wrapped key to `POST .../datakey/decrypt`. The service key
never leaves the KMS. `mytp_core.kms` (the mytp-core package) is the client (standard
library only); the field key provider `accounts.crypto.KMSKeyProvider` (story 2.12; a
re-export of `mytp_core.crypto.KMSKeyProvider`, either path works) builds on it. The
wrapped field keys live in `settings.env` (`FIELD_KMS_KEYS`, `BLIND_INDEX_KMS_KEY`), not
next to the data; each is unwrapped once per process (`datakey/decrypt`) and the plain key
stays in the process's memory only: never in a log, an error, Sentry, the database, the
cache or a file.

**Disaster recovery rests on the service key** (decisions 2026-10-14). There is no
plain-key escrow. Recovering the data needs both:

- (a) **the KMS service key and its domain, which must never be deleted.** **Deleting the
  service key, or losing the KMS domain, makes all personal data and its backups
  unrecoverable**, for good: every field key is wrapped by it.
- (b) **the wrapped values** of `FIELD_KMS_KEYS` and `BLIND_INDEX_KMS_KEY`: from
  `settings.env`, or, if the server is lost, from the password manager entry "MyTreatplan
  staging KMS". That entry holds the key ids and the wrapped values (wrapped only, never a
  plain key), updated at every generation and rotation. Restoring an older dump needs the
  wrapped values that were current when it was taken, so superseded wrapped values stay in
  the entry for 30 days after a rotation.

So:

- In OVHcloud IAM, restrict key deletion (and deactivation) to named administrators: only
  their own identities get a policy with `okms:apikms:serviceKey/delete` (and
  `deactivate`) on this domain; the server's identity never does, and no shared or root
  login is used for it.
- Never delete or deactivate the service key while any data or backup might still need it
  ("Rotate the service key").

**Daily check.** `kmscheck | kmscheck | *-*-* 04:00:00 UTC` in `jobs.conf`, so it runs via
`runjob` with Sentry check-ins. `manage.py kmscheck` gets the service key, generates a
256-bit data key, unwraps it and compares the bytes; it prints the key id and state, the
result and the certificate's expiry, and never the data key, the wrapped key or the
credential. It **fails 30 days before the certificate expires** (after still doing the
round trip), so Sentry alerts devops@mytreatplan.com in time to renew.

```sh
sudo systemctl start mytp-dev-job@kmscheck          # check now
sudo journalctl -u mytp-dev-job@kmscheck -n 50      # last runs
```

### Renew the access certificate

Do it when `kmscheck` reports "client certificate expires ..., within 30 days" (or
earlier). The new certificate goes in beside the old one, so nothing breaks meanwhile:

1. On the server, a new key and CSR (`-new` names, so the live files stay untouched):

   ```sh
   sudo -u mytp-dev sh -c 'umask 077 && openssl req -new -newkey rsa:3072 -nodes \
       -subj "/CN=mytp-kms-staging" \
       -keyout /srv/mytp-dev/shared/kms-client-new.key -out /srv/mytp-dev/shared/kms-client-new.csr'
   sudo cat /srv/mytp-dev/shared/kms-client-new.csr
   ```

2. KMS domain → *Generate an access certificate* with that CSR, validity 365 days,
   identity `mytp-kms-staging` only (the IAM policy applies to the identity, so it needs
   no change). Copy it to `/srv/mytp-dev/shared/kms-client-new.crt` (`install -m 600 -o mytp-dev
   -g mytp-dev`, as in step 11.3).
3. Test the new pair without touching `settings.env` (env vars beat the file):

   ```sh
   cd /srv/mytp-dev/current
   sudo -u mytp-dev KMS_CLIENT_CERT=/srv/mytp-dev/shared/kms-client-new.crt \
       KMS_CLIENT_KEY=/srv/mytp-dev/shared/kms-client-new.key ./venv/bin/python manage.py kmscheck
   ```

4. When it prints "round trip ok" with the new expiry, swap the files in:

   ```sh
   sudo -u mytp-dev mv /srv/mytp-dev/shared/kms-client-new.crt /srv/mytp-dev/shared/kms-client.crt
   sudo -u mytp-dev mv /srv/mytp-dev/shared/kms-client-new.key /srv/mytp-dev/shared/kms-client.key
   sudo rm /srv/mytp-dev/shared/kms-client-new.csr
   sudo systemctl restart mytp-dev-gunicorn            # new KMS calls use the new certificate
   sudo systemctl start mytp-dev-job@kmscheck
   ```

5. Update the expiry in the password manager reference entry and in the table above. Then delete the old certificate in the KMS panel (*Access certificates*),
   so the old private key no longer works anywhere.

If the private key may have leaked: do the same at once, and delete the old certificate
in the panel first.

### Rotate a field key

With the KMS provider (story 2.12), e.g. yearly, or at once if a key may have leaked.
Existing data stays readable throughout. Run each command yourself on the server, from
`/srv/mytp-dev/current`:

1. **New key, first.** `sudo -u mytp-dev ./venv/bin/python manage.py generate_kms_field_key
   kms<UTC date>T<time>` prints one entry, `<key_id>:<wrapped key>` (the plain key is
   discarded). Put it **first** in `FIELD_KMS_KEYS`, keeping the current entries after it
   (comma-separated, no spaces), and add the key id and its wrapped value to the reference
   entry "MyTreatplan staging KMS". Keep the superseded wrapped values there for 30 days
   after the rotation (older dumps need them), then remove them.
2. **Restart.** `manage.py check` (no issues), `manage.py kmscheck` (every key unwrap ok),
   then `sudo systemctl restart mytp-dev-gunicorn`. New values now use the new key; old ones
   still decrypt.
3. **Re-encrypt.** `manage.py reencrypt_fields` rewrites every value with the new key and
   recomputes the blind indexes; it fails if any value is still on an older key.
4. **Remove the old key** from `FIELD_KMS_KEYS`, and restart, once no backup encrypted
   with it can still be needed (30 days of dumps).

**Blind index key** (not rotated routinely; at once if it may have leaked):
`generate_kms_field_key --blind-index` prints a new wrapped value. Replace
`BLIND_INDEX_KMS_KEY` with it, restart, then run `reencrypt_fields` right away: email and
phone lookups fail between the restart and the end of that run. Turn signups off for that
window (`PORTAL_ENABLED=False` in `settings.env` before the restart; back to `True`, and
restart, once `reencrypt_fields` reports every value on the new key). If it aborts with an
`IntegrityError` on `email_index` anyway, delete the duplicate and run it again as in
step 12, "Move to the KMS provider", step 4.

### Rotate the service key

Not supported yet by the field key provider (story 2.12): it unwraps every
`FIELD_KMS_KEYS` entry and `BLIND_INDEX_KMS_KEY` with the one service key
`KMS_SERVICE_KEY_ID`, so pointing that setting at a new key makes every existing wrapped
key fail to unwrap (the app then fails on every personal field, and `kmscheck` alerts).
Re-wrapping under a new service key needs a follow-up story (a service key id per
wrapped key, or a re-wrap command). Until then, do **not** change `KMS_SERVICE_KEY_ID`
while field keys are wrapped by the current key. The outline below is for that story.
Rotation changes which key wraps the data keys; it never re-encrypts the data itself.

1. **New key.** In the KMS domain, generate a new service key with the same settings
   (`oct`, 256, wrapKey/unwrapKey/encrypt/decrypt, `SOFTWARE`), e.g. `mytp-staging-2`. The IAM policy
   covers the whole domain, so the server can use it at once.
2. **Switch.** Point `KMS_SERVICE_KEY_ID` in `/srv/mytp-dev/shared/settings.env` at the new
   key id and `sudo systemctl restart mytp-dev-gunicorn`. New data keys are now wrapped by the
   new key; `sudo systemctl start mytp-dev-job@kmscheck` must show the new id, state active.
   Old wrapped keys still name (or are stored with) the old key id and still unwrap.
3. **Re-wrap old data keys.** For each stored wrapped data key that the old key wrapped:
   unwrap it with the old key (`datakey/decrypt` on the old key id), then wrap the same
   plaintext with the new key (`encrypt` on the new key id), and store the new wrapped
   key and the new key id in place of the old ones, in one transaction per record. The
   data encrypted with that data key is untouched. A key wrapped with `encrypt` is
   unwrapped with `decrypt` (not `datakey/decrypt`), so that story must record how each data
   key was wrapped, and the IAM policy then also needs `okms:apikms:serviceKey/encrypt`
   and `okms:apikms:serviceKey/decrypt`. That story provides this as a management command;
   until every record is done, both keys stay active.
4. **Deactivate the old key only once nothing uses it**: no stored record and no job
   refers to it any more, and every backup taken before step 3 has expired (the SBG
   dumps keep 30 days, so wait at least 30 days after step 3). Deactivate it in the
   panel, never from code. **Never delete it** while any data, including a backup,
   might still need it: a deleted key makes every data key it wrapped, and so that
   data, unreadable for good.
5. Update the table above (current key) and the password manager entry.

## Sites on the staging host

`provision.sh <site> <domain> <htpasswd-user>` sets up one site. The site name is
lowercase letters, digits and `-`, starts with a letter, does not end with `-`, at most
20 characters; anything else is refused before any change. A run for one site never
touches another site's files, units, timers or Nginx config; packages, SSH hardening, the
firewall, certificate renewal and the release command are shared by the host.

| What | Name for site `<site>` |
|------|------------------------|
| Linux user (and group) | `mytp-<site>` |
| Home | `/srv/mytp-<site>` (`releases/`, `current`, `shared/`, `incoming/`) |
| Settings | `/srv/mytp-<site>/shared/settings.env` with `SITE=<site>` |
| App service | `mytp-<site>-gunicorn.service`, socket `mytp-<site>-gunicorn.socket` on `/run/mytp-<site>-gunicorn.sock` |
| Scheduled jobs | `mytp-<site>-job@<job>.timer` → `mytp-<site>-job@<job>.service` |
| Task worker | `mytp-<site>-worker.service`; Sentry monitor `mytp-<site>-worker` |
| Nginx | `/etc/nginx/sites-available/mytp-<site>.conf`, password file `/etc/nginx/mytp-<site>.htpasswd` |
| Deploy rights | `/etc/sudoers.d/mytp-<site>-deploy` |
| Deploy | `/usr/local/bin/mytp-release <site> <sha> /srv/mytp-<site>/incoming/<sha>.tar.gz`, as `mytp-<site>` |

The app reads `SITE` (default `dev`) and derives its server paths from it
(`SITE_HOME` = `/srv/mytp-<site>`: the provisioned `jobs.conf` copy and the backup temp
folder).

**Add a site** (e.g. `dev-ae`, `admindev`; each has its own ticket): DNS record, its own
database (never another site's), then from a copy of that repo's `deploy/`:
`bash ~/deploy/provision.sh <site> <domain> <htpasswd-user>`, its `settings.env` with
`SITE=<site>` (steps 2–4 above with the site's names), its deploy key in
`/srv/mytp-<site>/.ssh/authorized_keys`, and a CI job that uploads to
`/srv/mytp-<site>/incoming/` and runs the release command for `<site>` as `mytp-<site>`.
Running `provision.sh` from another repo also installs that repo's `release.sh` as the
host's release command, so keep the repos' copies the same. `release.sh` carries
`RELEASE_TOOL_VERSION` (raise it on every change): `provision.sh` never replaces an
installed release command with a lower version; it prints a WARNING, leaves the
installed one and goes on with the other steps. Re-run it from an up-to-date copy.

Gunicorn serves the WSGI module given to `provision.sh` as `WSGI_MODULE` (default
`mytreatplan.wsgi`, the portal); the admin site passes `WSGI_MODULE=mytp_admin.wsgi` on
every run (a run without it would point admindev's unit at the portal's module).

**Extra redirect domains (production EU only).** `EXTRA_DOMAINS` (space-separated names)
makes Nginx answer those names with a single 301 to `https://<domain>`, path and query
kept, on ports 80 and 443, without the password and without reaching the app. Only the
production EU site sets it, on every run:
`EXTRA_DOMAINS="mytreatplan.es www.mytreatplan.es mytreatplan.ie www.mytreatplan.ie"`.
No staging site sets it. Each name must look like a domain, differ from `<domain>` and
appear once, or the run stops (exit 2) before any change. The site's certificate (named
after `<domain>`) covers all the names: it is requested with `-d` for each, and a
certificate that lacks one is re-issued with `--expand`. Every name must resolve to the
host first; otherwise the run prints a WARNING and requests nothing. Port 80 answers the
ACME challenge for every name; the HTTPS redirect block is installed only once the
certificate covers them all. The value is not stored on the server: a run without it
removes the redirect and says so with a WARNING.

What the shared tooling expects from any site repo (the portal, the admin site
`mytreatplan/mytp-admin`):

- a Django project with `manage.py` and `requirements.txt` at the root (pip installs it
  from the release folder; `django-tasks-db==` in it makes `provision.sh` install the
  worker), a `locale/` folder (`compilemessages` refuses a project without one) and
  `/robots.txt` answering 200 (the release health check);
- a WSGI module exposing `application`, passed to `provision.sh` as `WSGI_MODULE`;
- `deploy/run-job.sh` and `deploy/jobs.conf` (the job units run the live release's
  `run-job.sh`, and `provision.sh` installs the timers from the `jobs.conf` beside it);
- a CI build that adds the git-ignored marker `deploy/release-ready` to the tarball last
  (the release command refuses a tarball without it, or without `manage.py`).

The admin site's repo carries only its `jobs.conf`, `run-job.sh` and
`staging.env.example`; provision `admindev` from a separate copy of the portal's merged
`deploy/` with the admin's `jobs.conf` and `staging.env.example` in it (the admin repo's
README has the commands), never from the copy used for `dev`.

**One-off, after the merge of epic-platform entry 10 (generic release check).** The
release command changed. Once the merge's deploy is green, run `provision.sh` for `dev`
again from a copy of the merged `deploy/` (no `WSGI_MODULE`: dev keeps
`mytreatplan.wsgi`, so its unit is unchanged): it reports `/usr/local/bin/mytp-release`
changed. The next deploy goes through the new release check. From then on, a release
built before the `deploy/release-ready` marker (any commit before this merge) cannot be
redeployed by re-running its old `deploy-staging` job: the release command refuses its
tarball. Go back to such a release with "Rollback" (it is still in `releases/`), or
deploy a new commit.

**Remove a site** (e.g. a throwaway one), as `debian`. Replace `<site>` with the name; it
must never be `dev` unless dev is meant to go:

```sh
site=<site>
for f in /etc/systemd/system/mytp-$site-job@*.timer; do
    [ -e "$f" ] && sudo systemctl disable --now "$(basename "$f")"
done
sudo systemctl disable --now mytp-$site-gunicorn.socket
sudo systemctl stop mytp-$site-gunicorn.service
sudo systemctl disable --now mytp-$site-worker.service
sudo rm -f /etc/systemd/system/mytp-$site-gunicorn.socket /etc/systemd/system/mytp-$site-gunicorn.service \
    /etc/systemd/system/mytp-$site-worker.service \
    /etc/systemd/system/mytp-$site-job@.service /etc/systemd/system/mytp-$site-job@*.timer
sudo systemctl daemon-reload
sudo rm -f /etc/nginx/sites-enabled/mytp-$site.conf /etc/nginx/sites-available/mytp-$site.conf \
    /etc/nginx/mytp-$site.htpasswd
sudo nginx -t && sudo systemctl reload nginx
sudo rm -f /etc/sudoers.d/mytp-$site-deploy && sudo visudo -c
sudo userdel mytp-$site
sudo rm -rf "/srv/mytp-$site"
```

Its database (if any) and certificate (`sudo certbot delete --cert-name <domain>`) are
removed separately.

### Add dev-ae

`dev-ae` (https://dev-ae.mytpdso.com) is the portal again with `REGION=ae`, beside `dev`
on the same host (epic-platform entry 7). The `deploy-staging` job deploys every master
build to both sites, one matrix leg per site (`fail-fast: false`, concurrency group
`deploy-staging-<site>`), so a failed dev-ae deploy never stops or rolls back dev, and the
reverse. Staging only: dev-ae's field keys are wrapped by dev's EU KMS service key, and it
uses dev's PostgreSQL service and backup bucket. No UAE KMS, storage or production
settings here (epic-uae-launch).

Until steps 1–8 are done, the dev-ae leg of `deploy-staging` is red ("No deploy key" or
the release command refusing an unprovisioned site); dev deploys as before. Run every
command yourself, in your own terminal. Nothing below prints a secret; never `cat` a
`settings.env`.

1. **DNS.** An A record `dev-ae.mytpdso.com` → the host's IPv4 (done; check with
   `dig +short dev-ae.mytpdso.com`).
2. **Database** (on dev's OVHcloud Managed PostgreSQL service; never dev's database). The
   three setup steps every new site database needs:
   1. *The right service.* In the panel of the service that serves dev, add a database
      `mytp_dev_ae` and a user `mytp_dev_ae`; keep the user's password in the team
      password manager. The host and port are that service's, from the panel.
   2. *Authorized IPs.* The staging host's IPv4 is in that service's authorized IPs
      (already true for dev's service; check it in the panel).
   3. *Owners.* The panel creates the database owned by the service admin (`avnadmin`),
      and PostgreSQL 15+ then refuses `mytp_dev_ae` in schema `public`. From the server,
      as the service admin (psql asks for its password):

      ```sh
      psql "host=<db-host> port=<db-port> dbname=mytp_dev_ae user=avnadmin sslmode=require" \
          -c 'ALTER DATABASE mytp_dev_ae OWNER TO mytp_dev_ae' \
          -c 'ALTER SCHEMA public OWNER TO mytp_dev_ae'
      ```
3. **Provision**, as `debian`, from a copy of the portal's merged `deploy/` (the same copy
   as for dev; no `WSGI_MODULE`):

   ```sh
   bash ~/deploy/provision.sh dev-ae dev-ae.mytpdso.com mytp-team
   ```

   The first run asks for the basic auth password of `mytp-team` (not echoed; use the
   team password); later runs can take `< /dev/null`. It creates `mytp-dev-ae`, `/srv/mytp-dev-ae`, the
   units, timers, sudoers, Nginx site and certificate. The job timers fail ("no live
   release") until the first deploy; that is expected.
4. **Files from dev.** The database CA and the KMS access certificate and key, copied on
   the server only (the KMS key never leaves the host):

   ```sh
   sudo install -m 640 -o mytp-dev-ae -g mytp-dev-ae /srv/mytp-dev/shared/db-ca.pem /srv/mytp-dev-ae/shared/db-ca.pem
   for f in kms-client.crt kms-client.key; do
       sudo install -m 600 -o mytp-dev-ae -g mytp-dev-ae /srv/mytp-dev/shared/$f /srv/mytp-dev-ae/shared/$f
   done
   sudo ls -l /srv/mytp-dev-ae/shared/      # db-ca.pem 640, kms-client.* 600, all mytp-dev-ae
   ```

   When dev's KMS certificate is renewed ("Renew the access certificate"), copy the new
   pair here again.
5. **settings.env**, built on the server from dev's without showing any value
   (`deploy/staging.env.example` lists the dev-ae differences). Dev's lines that differ
   for dev-ae are left out, then dev-ae's are added with a new `SECRET_KEY`:

   ```sh
   sudo install -m 600 -o mytp-dev-ae -g mytp-dev-ae /dev/null /srv/mytp-dev-ae/shared/settings.env
   sudo sh -c "grep -v -E '^(SITE|REGION|SECRET_KEY|DATABASE_URL|ALLOWED_HOSTS|CSRF_TRUSTED_ORIGINS|SITE_URL|DB_SSLROOTCERT|BACKUP_PREFIX|KMS_CLIENT_CERT|KMS_CLIENT_KEY|FIELD_ENCRYPTION_KEYS|BLIND_INDEX_KEY|FIELD_KEY_PROVIDER|FIELD_KMS_KEYS|BLIND_INDEX_KMS_KEY|STORAGE_[A-Z_]+|REGION_SITE_URL_[A-Z]+)=' \
       /srv/mytp-dev/shared/settings.env > /srv/mytp-dev-ae/shared/settings.env"
   sudo -u mytp-dev-ae sh -ec 'k=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
   printf "%s\n" SITE=dev-ae REGION=ae "SECRET_KEY=$k" \
       ALLOWED_HOSTS=dev-ae.mytpdso.com CSRF_TRUSTED_ORIGINS=https://dev-ae.mytpdso.com \
       SITE_URL=https://dev-ae.mytpdso.com DB_SSLROOTCERT=/srv/mytp-dev-ae/shared/db-ca.pem \
       BACKUP_PREFIX=staging-dev-ae/ \
       KMS_CLIENT_CERT=/srv/mytp-dev-ae/shared/kms-client.crt \
       KMS_CLIENT_KEY=/srv/mytp-dev-ae/shared/kms-client.key \
       FIELD_ENCRYPTION_KEYS= BLIND_INDEX_KEY= STORAGE_SAMPLE_UPLOADS=False \
       REGION_SITE_URL_EU=https://dev.mytpdso.com \
       >> /srv/mytp-dev-ae/shared/settings.env'
   ```

   Then the database URL. This asks for the host and port (from the panel) and the
   `mytp_dev_ae` password (not echoed), URL-encodes the password (a password with
   `@ : / # % &` breaks a URL typed by hand) and appends the line:

   ```sh
   sudo -u mytp-dev-ae python3 -c '
   import getpass, urllib.parse
   host = input("Database host: ").strip()
   port = input("Database port: ").strip()
   password = urllib.parse.quote(getpass.getpass("mytp_dev_ae password: "), safe="")
   with open("/srv/mytp-dev-ae/shared/settings.env", "a") as f:
       f.write(f"DATABASE_URL=postgres://mytp_dev_ae:{password}@{host}:{port}/mytp_dev_ae?sslmode=require\n")
   '
   ```

   `DB_SSLMODE=verify-full` (kept from dev's file) still applies on top of the URL's
   `sslmode=require`, with dev-ae's copy of the CA. Check the three database steps with
   psql before the first deploy (prints names only):

   ```sh
   sudo -u mytp-dev-ae sh -ec 'url=$(sed -n "s/^DATABASE_URL=//p" /srv/mytp-dev-ae/shared/settings.env)
   psql "$url" -qAt -c "SELECT current_user, current_database(),
       (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()),
       (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = '\''public'\'')" \
       -c "CREATE TABLE mytp_setup_check (x int)" -c "DROP TABLE mytp_setup_check"'
   # mytp_dev_ae|mytp_dev_ae|mytp_dev_ae|mytp_dev_ae
   sudo grep -c -E '^(SITE=dev-ae|REGION=ae|SECRET_KEY=.|DATABASE_URL=.)' /srv/mytp-dev-ae/shared/settings.env   # 4
   ```

   No dev value may be left over from dev's file (counts only; `REGION_SITE_URL_EU`
   points at dev on purpose, so it is left out of the counts):

   ```sh
   sudo grep -v '^REGION_SITE_URL_EU=' /srv/mytp-dev-ae/shared/settings.env | grep -c '/srv/mytp-dev/'                 # 0
   sudo grep -v '^REGION_SITE_URL_EU=' /srv/mytp-dev-ae/shared/settings.env | grep -c -E '^SITE=dev$|dev\.mytpdso\.com'  # 0
   sudo grep -v '^REGION_SITE_URL_EU=' /srv/mytp-dev-ae/shared/settings.env | grep -n -o -E '^[A-Z_]+=.*(/srv/mytp-dev/|dev\.mytpdso\.com)' | cut -d= -f1   # names only
   ```

   If a count is not 0, the last command lists the setting names (never the values);
   fix those lines with `sudo -u mytp-dev-ae nano /srv/mytp-dev-ae/shared/settings.env`
   (dev-ae's paths are under `/srv/mytp-dev-ae/`, its host is `dev-ae.mytpdso.com`).

   `password authentication failed` means the wrong service (the host) or password; a
   hang means the staging IP is not in the service's authorized IPs; `permission denied
   for schema public` means step 2.3 was skipped.
6. **Backup retention.** dev-ae's dumps go to dev's bucket under `staging-dev-ae/`, but
   the bucket's only lifecycle rule (step 10.3) expires `staging/` only, and the
   server's S3 user cannot delete. As the bucket owner, as in step 10.3 (your own
   terminal, the admin user's keys in `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`),
   put both rules: `put-bucket-lifecycle-configuration` replaces the whole set, so the
   `staging/` rule is repeated unchanged.

   ```sh
   export AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
   aws --endpoint-url https://s3.sbg.io.cloud.ovh.net --region sbg s3api put-bucket-lifecycle-configuration \
       --bucket mytp-backups-staging --lifecycle-configuration '{"Rules": [{
         "ID": "expire-dumps-30d", "Status": "Enabled", "Filter": {"Prefix": "staging/"},
         "Expiration": {"Days": 30},
         "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 2}}, {
         "ID": "expire-dev-ae-dumps-30d", "Status": "Enabled", "Filter": {"Prefix": "staging-dev-ae/"},
         "Expiration": {"Days": 30},
         "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 2}}]}'
   aws --endpoint-url https://s3.sbg.io.cloud.ovh.net --region sbg s3api get-bucket-lifecycle-configuration \
       --bucket mytp-backups-staging      # both rules: staging/ and staging-dev-ae/, 30 days
   ```

7. **dev-ae's own field keys.** A new environment starts on the KMS provider: no plain
   keys. `generate_kms_field_key` needs the portal's code, and dev-ae has no release yet
   (the first deploy's `migrate` refuses to run without keys), so use a throwaway copy of
   dev's live release with dev-ae's `settings.env`:

   ```sh
   sudo -u mytp-dev-ae install -d -m 700 /srv/mytp-dev-ae/keygen
   sudo tar -C /srv/mytp-dev/current --exclude=./venv --exclude=./settings.env --exclude=./static_root -cf - . \
       | sudo -u mytp-dev-ae tar -C /srv/mytp-dev-ae/keygen -xf -
   sudo -u mytp-dev-ae sh -ec 'cd /srv/mytp-dev-ae/keygen
   ln -s /srv/mytp-dev-ae/shared/settings.env settings.env
   python3 -m venv venv
   ./venv/bin/pip install --quiet --disable-pip-version-check --no-cache-dir -r requirements.txt
   umask 077
   f=$(./venv/bin/python manage.py generate_kms_field_key "ae$(date -u +%Y%m%dT%H%M)")
   b=$(./venv/bin/python manage.py generate_kms_field_key --blind-index)
   printf "FIELD_KEY_PROVIDER=accounts.crypto.KMSKeyProvider\nFIELD_KMS_KEYS=%s\nBLIND_INDEX_KMS_KEY=%s\n" "$f" "$b" \
       >> /srv/mytp-dev-ae/shared/settings.env
   ./venv/bin/python manage.py check
   ./venv/bin/python manage.py kmscheck'
   sudo rm -rf /srv/mytp-dev-ae/keygen
   ```

   `kmscheck` ends with the round trip and each key's unwrap `ok`. Copy dev-ae's key ids
   and wrapped values (never a plain key) to the password manager under a "dev-ae"
   heading of the entry "MyTreatplan staging KMS" (as in step 12): they are wrapped by
   the same staging service key as dev's.
8. **Deploy key and secret.** On your machine:

   ```sh
   ssh-keygen -t ed25519 -N '' -C 'github-actions deploy-staging dev-ae' -f ./mytp_deploy_staging_dev_ae
   cat mytp_deploy_staging_dev_ae.pub | ssh debian@<instance-ip> 'sudo tee -a /srv/mytp-dev-ae/.ssh/authorized_keys >/dev/null'
   ssh -i ./mytp_deploy_staging_dev_ae -o IdentitiesOnly=yes mytp-dev-ae@<instance-ip> true
   ```

   The key belongs to `mytp-dev-ae` only (never add it to dev's `authorized_keys`, nor
   dev's key here). In GitHub, add the secret `STAGING_SSH_KEY_DEV_AE` with the contents
   of `mytp_deploy_staging_dev_ae` (repository → Settings → Secrets and variables →
   Actions; an environment secret of `staging`, beside the other three, works the same).
   `STAGING_HOST` and `STAGING_KNOWN_HOSTS` are shared with dev. Then delete the local
   private key.
9. **Turnstile.** Cloudflare dashboard → Turnstile → dev's widget → *Hostname
   management*: add `dev-ae.mytpdso.com`. dev-ae reuses the widget's keys (they came with
   dev's `settings.env` in step 5).
10. **First deploy.** Merge to `master`, or re-run the failed dev-ae leg of the last
   `deploy-staging` run (it skips if `master` has moved on; the next push deploys). When
   it is green, re-run step 3 (with `< /dev/null`) so `provision.sh` sees the live
   release and installs `mytp-dev-ae-worker`.
11. **Checks.**

    ```sh
    curl -s https://dev-ae.mytpdso.com/healthz       # ok, site: dev-ae, region: ae
    curl -s https://dev.mytpdso.com/healthz          # ok, site: dev, region: eu (unchanged)
    curl -sI https://dev-ae.mytpdso.com/             # 401 without the team password
    systemctl list-timers 'mytp-dev-ae-job@*'        # the jobs.conf timers
    systemctl is-active mytp-dev-ae-worker           # active
    cd /srv/mytp-dev-ae/current && sudo -u mytp-dev-ae ./venv/bin/python manage.py kmscheck   # ok
    ```

    Both sites run the same sha (`readlink /srv/mytp-dev/current /srv/mytp-dev-ae/current`).
    Sign up once on dev-ae, then compare user counts (counts only):

    ```sh
    for s in dev dev-ae; do
        (cd /srv/mytp-$s/current && sudo -u mytp-$s ./venv/bin/python manage.py shell -c \
            "from django.contrib.auth import get_user_model; print('$s', get_user_model().objects.count())")
    done
    ```

    Only dev-ae's count goes up by one. Sentry monitors for dev-ae's jobs
    (`mytp-dev-ae-*`) wait for the plan upgrade, like dev's and admindev's; dev-ae has no
    Sentry environment of its own yet, and no restore test of its own (entry 13).

**Region site URLs (epic-regions entry 1).** The country map sends a person whose
country the other region serves to that region's site: dev sends them to dev-ae and
dev-ae to dev, not to the production domains. On sites set up before this change, add
the one line to each `settings.env` (no secret; prints nothing) and restart both sites'
gunicorn and worker (the worker reads the same `settings.env`):

```sh
sudo -u mytp-dev sh -c 'echo REGION_SITE_URL_AE=https://dev-ae.mytpdso.com >> /srv/mytp-dev/shared/settings.env'
sudo -u mytp-dev-ae sh -c 'echo REGION_SITE_URL_EU=https://dev.mytpdso.com >> /srv/mytp-dev-ae/shared/settings.env'
sudo grep -c '^REGION_SITE_URL_' /srv/mytp-dev/shared/settings.env /srv/mytp-dev-ae/shared/settings.env   # 1 each
sudo systemctl restart mytp-dev-gunicorn mytp-dev-worker mytp-dev-ae-gunicorn mytp-dev-ae-worker
```

To rotate dev-ae's deploy key, repeat step 8 and replace the old line in
`/srv/mytp-dev-ae/.ssh/authorized_keys`. To remove dev-ae, see "Remove a site" above,
then drop the `dev-ae` leg from the `deploy-staging` matrix.

## Move dev to the per-site layout

One-off, for the change that made the tooling site-aware (epic-platform entry 1). Before
it, dev ran as user `mytp` from `/srv/mytp`, with `mytp-gunicorn`, `mytp-job@*` timers,
Nginx site `mytp.conf`, `/etc/nginx/mytp.htpasswd` and `/etc/sudoers.d/mytp-deploy`. The
move is in place on the same host and database (the database and its contents do not
change). dev is down for about 5 minutes, from step 3 until the deploy in step 7 is
green. `/srv/mytp` stays one day as a fallback; step 10 deletes it.

Run every command yourself, as `debian` on the server unless it says otherwise. No
secret is printed: settings files are copied and edited in place, and only counts or the
`SITE` line are shown.

1. **Merge the pull request.** Its `deploy-staging` job fails at "Upload release"
   (`mytp-dev` does not exist yet, so SSH is refused) and changes nothing: dev keeps
   serving from `/srv/mytp`. Merge nothing else until step 7, and tell the team about the
   short downtime.
2. **Copy the merged `deploy/` to the server** (from your machine, in an up-to-date
   `master` checkout):

   ```sh
   ssh debian@<instance-ip> 'rm -rf ~/deploy'
   scp -r deploy debian@<instance-ip>:~/
   ```

3. **Stop the old site** (downtime starts):

   ```sh
   for f in /etc/systemd/system/mytp-job@*.timer; do sudo systemctl disable --now "$(basename "$f")"; done
   sudo systemctl disable --now mytp-gunicorn.socket
   sudo systemctl stop mytp-gunicorn.service
   sudo rm /etc/nginx/sites-enabled/mytp.conf        # the file stays in sites-available
   sudo nginx -t && sudo systemctl reload nginx
   ```

4. **Carry the password file over and provision `dev`.** With the copied password file
   `provision.sh` asks for no password; the certificate for `dev.mytpdso.com` already
   exists, so the Nginx site gets HTTPS at once.

   ```sh
   sudo install -m 640 -o root -g www-data /etc/nginx/mytp.htpasswd /etc/nginx/mytp-dev.htpasswd
   bash ~/deploy/provision.sh dev dev.mytpdso.com <htpasswd-user>
   ```

   It reports changes (release command, user `mytp-dev`, folders, sudoers, units,
   timers, Nginx site) and asks for `settings.env`.
5. **Carry the shared files over** (`settings.env`, `db-ca.pem`, `kms-client.crt`,
   `kms-client.key`; not `jobs.conf`, which provision wrote), then point the paths in
   `settings.env` at the new home and add `SITE=dev`:

   ```sh
   sudo find /srv/mytp/shared -maxdepth 1 -type f ! -name jobs.conf -printf '%f\n'   # names only
   for f in $(sudo find /srv/mytp/shared -maxdepth 1 -type f ! -name jobs.conf -printf '%f\n'); do
       sudo install -m "$(sudo stat -c %a "/srv/mytp/shared/$f")" -o mytp-dev -g mytp-dev \
           "/srv/mytp/shared/$f" "/srv/mytp-dev/shared/$f"
   done
   sudo sed -i 's#/srv/mytp/#/srv/mytp-dev/#g' /srv/mytp-dev/shared/settings.env
   sudo grep -q '^SITE=' /srv/mytp-dev/shared/settings.env \
       || printf '\nSITE=dev\n' | sudo tee -a /srv/mytp-dev/shared/settings.env >/dev/null
   sudo chown mytp-dev:mytp-dev /srv/mytp-dev/shared/settings.env
   sudo chmod 600 /srv/mytp-dev/shared/settings.env
   sudo grep -c '/srv/mytp/' /srv/mytp-dev/shared/settings.env    # 0
   sudo grep -x 'SITE=dev' /srv/mytp-dev/shared/settings.env       # SITE=dev
   sudo ls -l /srv/mytp-dev/shared/                                # all mytp-dev mytp-dev
   ```

6. **Deploy key.** The same key (GitHub's `STAGING_SSH_KEY` does not change) now logs in
   as `mytp-dev`:

   ```sh
   sudo install -m 600 -o mytp-dev -g mytp-dev /srv/mytp/.ssh/authorized_keys /srv/mytp-dev/.ssh/authorized_keys
   ```

7. **Deploy.** GitHub → Actions → the merge commit's CI run → **Re-run failed jobs**.
   When `deploy-staging` is green, dev serves from `/srv/mytp-dev` (downtime ends).
8. **Checks.**

   ```sh
   readlink /srv/mytp-dev/current                  # /srv/mytp-dev/releases/<merge sha>
   systemctl is-active mytp-dev-gunicorn.socket    # active
   systemctl list-timers 'mytp-dev-job@*'          # one per line of jobs.conf
   systemctl list-timers 'mytp-job@*'              # 0 timers listed
   sudo systemctl start mytp-dev-job@clearsessions # Sentry Crons: mytp-dev-clearsessions ok
   bash ~/deploy/provision.sh dev dev.mytpdso.com <htpasswd-user>   # No changes.
   ```

   From your machine: `curl -s -o /dev/null -w '%{http_code}\n' https://dev.mytpdso.com/healthz`
   prints `200`, and the checks of step 7 under "First-time setup" pass.

   Then a **throwaway second site** proves sites stay apart (no DNS: it skips the
   certificate with a warning; type any password, the site is removed right after):

   ```sh
   sudo find /etc/systemd/system /etc/nginx /etc/sudoers.d -name '*mytp-dev*' -exec md5sum {} + | sort > ~/dev-before.txt
   bash ~/deploy/provision.sh tmpcheck tmpcheck.invalid team
   id mytp-tmpcheck && ls /srv/mytp-tmpcheck                 # its own user and home
   systemctl list-timers 'mytp-tmpcheck-job@*'              # its own timers
   ls /etc/nginx/sites-enabled/                              # mytp-dev.conf and mytp-tmpcheck.conf
   bash ~/deploy/provision.sh dev dev.mytpdso.com <htpasswd-user>   # No changes.
   sudo find /etc/systemd/system /etc/nginx /etc/sudoers.d -name '*mytp-dev*' -exec md5sum {} + | sort | diff ~/dev-before.txt - && echo "dev unchanged"
   ```

   Remove it with "Remove a site" above (`site=tmpcheck`), then `rm ~/dev-before.txt`.
   Its timers fire only on schedule, and with no release they would just fail, so remove
   it the same day.
9. **Fallback, while `/srv/mytp` exists** (only if dev does not work on the new layout).
   Do not merge to `master` while in fallback. The first command blocks deploys: without
   it, the next deploy would still log in as `mytp-dev`, migrate the shared dev database
   and bring `mytp-dev-gunicorn` back up.

   ```sh
   sudo mv /srv/mytp-dev/.ssh/authorized_keys /srv/mytp-dev/.ssh/authorized_keys.disabled
   for f in /etc/systemd/system/mytp-dev-job@*.timer; do sudo systemctl disable --now "$(basename "$f")"; done
   sudo systemctl disable --now mytp-dev-gunicorn.socket
   sudo systemctl stop mytp-dev-gunicorn.service
   sudo rm /etc/nginx/sites-enabled/mytp-dev.conf
   sudo ln -s /etc/nginx/sites-available/mytp.conf /etc/nginx/sites-enabled/mytp.conf
   sudo nginx -t && sudo systemctl reload nginx
   sudo systemctl enable --now mytp-gunicorn.socket
   for f in /etc/systemd/system/mytp-job@*.timer; do sudo systemctl enable --now "$(basename "$f")"; done
   ```

   dev then serves the release it had before the merge, and deploys are blocked (CI can
   no longer log in as `mytp-dev`). To retry once the new layout is fixed, put the key
   back with `sudo mv /srv/mytp-dev/.ssh/authorized_keys.disabled
   /srv/mytp-dev/.ssh/authorized_keys`, then repeat step 3, the `provision.sh` command of
   step 4 (it re-enables dev's units, timers and Nginx site), and step 7.
10. **One day later: delete the old layout.**

    ```sh
    for f in /etc/systemd/system/mytp-job@*.timer; do sudo systemctl disable --now "$(basename "$f")"; done
    sudo systemctl disable --now mytp-gunicorn.socket
    sudo systemctl stop mytp-gunicorn.service
    sudo rm -f /etc/systemd/system/mytp-job@*.timer /etc/systemd/system/mytp-job@.service \
        /etc/systemd/system/mytp-gunicorn.socket /etc/systemd/system/mytp-gunicorn.service
    sudo systemctl daemon-reload
    sudo systemctl reset-failed
    sudo rm -f /etc/nginx/sites-enabled/mytp.conf /etc/nginx/sites-available/mytp.conf /etc/nginx/mytp.htpasswd
    sudo nginx -t && sudo systemctl reload nginx
    sudo rm -f /etc/sudoers.d/mytp-deploy && sudo visudo -c
    sudo userdel mytp                 # its group goes with it
    sudo rm -rf /srv/mytp             # exactly this path: /srv/mytp-dev is the live site
    ```

    The `mytp-job@*` globs never match `mytp-dev-job@*`. Check:

    ```sh
    systemctl list-units --all 'mytp-gunicorn*' 'mytp-job@*'   # 0 loaded units listed
    ls /etc/nginx/sites-enabled/ /etc/sudoers.d/               # mytp-dev.conf, mytp-dev-deploy; no mytp.conf, no mytp-deploy
    id mytp                                                    # no such user
    ```

    and `https://dev.mytpdso.com/healthz` still answers `200`. Record the move in the pull
    request ("dev moved to the per-site layout <date>, /srv/mytp deleted <date>, <who>").

## Interface with the admin site

The admin site (`admindev` on staging) and each portal site call each other on `/api/v1/`
(epic-admin-interface, AD-4; the `integration` app). Every request is signed with the
caller's own Ed25519 key (mytp-core's `mytp_core.interface`: a JWT with the issuer, the
audience, a 60-second life, a one-time id and the hash of the request) and checked
against the caller's public key, so Nginx's `/api/` block has no team password (bodies up
to 1 MB; production adds an IP allowlist, epic-eu-golive). A refused call is a 401 with
only a code (`missing`, `signature`, `unknown_key`, `audience`, `expired`,
`request_hash`, `replayed`); a site without a signing key answers 503 (`unavailable`).

| What | Where |
|------|-------|
| Signing key | `INTERFACE_SIGNING_KMS_KEY=<kid>:<wrapped>` in `shared/settings.env`: a KMS data key (staging service key) used as the Ed25519 seed, unwrapped once per process, never logged; checked by the daily `kmscheck` |
| Admin site's public keys | `INTERFACE_PEER_KEYS=admindev:<kid>:<public key>[,...]` |
| Admin site's address | `INTERFACE_PEERS=admindev=https://admindev.mytpdso.com` |
| Call log | table `integration_interfacecall` (time, issuer, key id, method, path, accepted, reason, status, IP), every request to `/api/`; refused ones also as a warning line in `journalctl -u mytp-dev-gunicorn`; deleted after 90 days by the daily `purgeinterfacecalls` job |

The `purgeinterfacecalls` job (04:20 UTC daily) and the `/api/` Nginx block reach a site
only when `provision.sh` runs again after the merge's deploy (see [Sites on the staging
host](#sites-on-the-staging-host)); admindev's copy of `deploy/` needs the same. After
its first run, disable its Sentry monitor `mytp-<site>-purgeinterfacecalls` as for the
other jobs.

### Set up the keys (dev and admindev)

Public keys are not secret; the wrapped key stays in `settings.env` only (it is useless
without the KMS service key). Run each command yourself, in your own terminal on the
server, once the release with the `integration` app is live on dev and the one with the
`interface` app on admindev:

1. **dev: generate its signing key** straight into the settings file, and print only its
   public key:

   ```sh
   cd /srv/mytp-dev/current
   sudo -u mytp-dev sh -ec 'umask 077
   out=$(./venv/bin/python manage.py generate_interface_signing_key "sig$(date -u +%Y%m%d)")
   printf "%s\n" "$out" | grep "^INTERFACE_SIGNING_KMS_KEY=" >> /srv/mytp-dev/shared/settings.env
   printf "%s\n" "$out" | grep "^INTERFACE_PUBLIC_KEY="'
   sudo grep -c '^INTERFACE_SIGNING_KMS_KEY=.' /srv/mytp-dev/shared/settings.env   # 1
   ```

   The last line printed is `INTERFACE_PUBLIC_KEY=dev:<kid>:<public key>`. (Delete an
   empty `INTERFACE_SIGNING_KMS_KEY=` line from the file first, if it has one.)
2. **admindev: the same** (its README, "Interface with the portals"), which prints
   `INTERFACE_PUBLIC_KEY=admindev:<kid>:<public key>`; then on admindev
   `manage.py add_peer_key eu <dev's kid> <dev's public key> --site dev`.
3. **dev: add admindev's public key and address** to `shared/settings.env`, as `debian`
   with `sudoedit /srv/mytp-dev/shared/settings.env`:

   ```sh
   INTERFACE_PEERS=admindev=https://admindev.mytpdso.com
   INTERFACE_PEER_KEYS=admindev:<admindev's kid>:<admindev's public key>
   ```

   then `sudo systemctl restart mytp-dev-gunicorn` and
   `sudo -u mytp-dev ./venv/bin/python manage.py check` (no issues).
4. **Ping each way** and look at both logs:

   ```sh
   # on dev
   sudo -u mytp-dev ./venv/bin/python manage.py ping_peer admindev    # {"region": null, "site": "admindev", "time": ...}
   # on admindev
   sudo -u mytp-admindev ./venv/bin/python manage.py ping_peer dev    # {"region": "eu", "site": "dev", "time": ...}
   sudo -u mytp-dev ./venv/bin/python manage.py shell -c "from integration.models import InterfaceCall as C; print([(c.iss, c.kid, c.accepted, c.reason, c.status) for c in C.objects.all()[:3]])"
   ```

   A failure prints one line (`ping_peer admindev: HTTP 401 (unknown_key)`, `... no
   answer: timed out`, `... No signing key ...`) and exits 1.
5. **Replay check**: a request sent twice is refused. On admindev, sign one GET of dev's
   ping and send it twice with curl within its minute (the token lives 60 seconds and
   stays in your terminal only):

   ```sh
   # on admindev
   cd /srv/mytp-admindev/current
   t=$(sudo -u mytp-admindev ./venv/bin/python manage.py shell --no-imports -c "from mytp_core import interface; print(interface.sign_request('GET', '/api/v1/ping', b'', audience='dev'))")
   curl -s -H "Authorization: MyTP-Signature $t" https://dev.mytpdso.com/api/v1/ping   # 200
   curl -s -H "Authorization: MyTP-Signature $t" https://dev.mytpdso.com/api/v1/ping   # 401 replayed
   ```

   dev's log then shows the second call refused (`reason` `replayed`) and the journal a
   `interface call refused: replayed` line.

dev-ae: the same steps with `mytp-dev-ae`, its own key (`INTERFACE_PUBLIC_KEY=dev-ae:...`)
and, on admindev, `add_peer_key ae <kid> <public key> --site dev-ae`.

### Rotate the signing key

1. Generate the new key as in step 1 but print it only (no `>>`); give its public key to
   the admin site (`add_peer_key`), which then knows both kids.
2. Replace the `INTERFACE_SIGNING_KMS_KEY` line with the new one and restart Gunicorn;
   `ping_peer admindev` passes with the new kid.
3. On admindev, `retire_peer_key eu <old kid>`.

To rotate admindev's key, add its new public key to `INTERFACE_PEER_KEYS` (comma-separated,
both kids), restart, let admindev switch, then remove the old entry and restart again.

### Outbox

Events (client records, entry 8; contact messages and the accounting feed later) go from
the portal to the admin site through an outbox (epic-admin-interface
entry 3, AD-3, AD-18, AD-19; `integration.outbox`). Code that changes something the admin
site must know calls `outbox.record(type, aggregate_id, aggregate_version, payload)` in
its own transaction: the payload is checked against the type's closed model (mytp-core
`contracts.payloads`) and the central payload checks, and the row
(`integration_outboxevent`) commits or rolls back with the caller's change.

| What | Where |
|------|-------|
| Sending | `dispatchoutbox` job every minute (`manage.py dispatch_outbox`), plus one short (10 s) task on the worker after each transaction that recorded events; one signed `POST <admin>/api/v1/events` per event, to the only `INTERFACE_PEERS` entry (or `INTERFACE_ADMIN_SITE`) |
| Order | per aggregate, the lowest pending `sequence` only (numbered by the aggregate's counter row, `integration_outboxaggregate`): a failing event holds back its aggregate's later events, never other aggregates' |
| Answers | `accepted`, `duplicate`, `stale` → `delivered`; `rejected` → `rejected` (final, listed on the admin site under "Interface events"); refused here before sending (over 256 KB, no longer valid) → `rejected` (final, `last_error` says why, not listed on the admin site, so a warning line and one Sentry warning "Outbox event rejected before sending" with the event id, type and reason, never the payload); anything else → retried. A run stops after 3 failures in a row that say the admin site is down (no answer, 5xx, 401); failures of single events don't stop it |
| Retries | for ever, after min(2^(attempts-1), 60) minutes; `attempts`, `next_attempt_at` and `last_error` (a code and a short text, never the payload) on the row; one Sentry warning per event failing 24 hours (event id, type, error code) |
| Retention | delivered rows deleted after 30 days by the daily `purgeoutbox` job (04:25 UTC), payload included; rejected and pending rows are kept |
| Logs | `journalctl -u mytp-dev-worker` and the job's journal: one line per event recorded, delivered, rejected or not delivered (event id, type, error code) |

A dispatcher killed mid-send leaves the row pending; the next run sends it again and the
admin site answers `duplicate` (it keeps every event id). With pending events but no
admin site or signing key configured, `dispatch_outbox` fails with one line, so the
job's Sentry monitor `mytp-<site>-dispatchoutbox` shows it; with nothing pending it needs
no configuration. The two jobs reach a site only when `provision.sh` runs again after the
merge's deploy (see [Sites on the staging host](#sites-on-the-staging-host)); keep the
Sentry monitor of `dispatchoutbox` enabled (a minute schedule: it alerts when the job
fails or stops), and disable `purgeoutbox`'s after its first run as for the other daily
jobs.

Look at the outbox (counts by state, the oldest pending; no payload):

```sh
sudo -u mytp-dev ./venv/bin/python manage.py shell -c "from django.db.models import Count; from integration.models import OutboxEvent as E; print(list(E.objects.values_list('state').annotate(Count('id')))); print(E.objects.filter(state='pending').values_list('event_id', 'type', 'attempts', 'next_attempt_at', 'last_error').first())"
```

A stopped admin site: with admindev's Gunicorn stopped, a recorded event stays pending with
`attempts` and `last_error` growing (1, 2, 4 ... minutes apart); once it is started again,
the next run delivers it. (The staging-only sample event of entry 3, `emit_sample_event`
and `INTERFACE_SAMPLE_EVENTS`, are gone: remove the setting from `shared/settings.env`.)

#### Client records

The admin site keeps a copy of every doctor (client) registered here (epic-admin-interface
entry 8, AD-2, AD-15, AD-18; `accounts.client_sync`). Each client write records one event
in the outbox, in the write's own transaction, with the account's `uid` as the aggregate
and its `sync_version` (bumped under the row's lock) as the version:

| Write | Event |
|-------|-------|
| Signup completed | `client.updated` (title, names, email, mobile, country, home region, active, joined, how found; never the "Others" text) |
| A change in the Django admin (add or change) | `client.updated`; for a staff account nothing, or `client.erased` once it was a client (staff are not clients) |
| Erase account (Django admin action) | `client.erased` (its `client.updated` rows still pending are deleted first): admindev deletes its copy; nothing for an account never sent |
| Password, sign-in, sessions, re-encryption | nothing |
| The purge of erased accounts | nothing sent; every outbox row of the purged accounts is deleted |

The outbox stores every payload encrypted with this site's field keys
(`integration_outboxevent.payload`, decrypted only to build the envelope it sends;
`reencrypt_fields` covers it). The payload goes in clear over the signed channel
(admindev encrypts it with its own keys) and is never logged; the outbox's lines carry
the event id, type and aggregate only.

**Re-sync.** admindev's `resync_client <uid>` sends a signed `POST /api/v1/clients/resync`
(`{"aggregate_id": "<uid>"}`, admindev only); the portal answers 202 and a worker task
emits the account's current state as its next version (`client.updated`, or
`client.erased` again for an erased account). An unknown uid (or a staff or erased
account never sent) is 404 `unknown_client`; any other body than
`{"aggregate_id": "<uuid>"}` is 400 `invalid_body`. Log lines: `integration.clients` (uid, outcome).

**Backfill** (once per site after the deploy that adds the sync, and whenever admindev's
copy must be rebuilt): every account, each in its own transaction, then the outbox
delivers them.

```sh
cd /srv/mytp-dev/current
sudo -u mytp-dev ./venv/bin/python manage.py sync_clients --all       # "N client.updated, M client.erased, K skipped (not sent: staff or erased)"
sudo -u mytp-dev ./venv/bin/python manage.py sync_clients <uid> ...   # some accounts only
```

An account whose stored state the contract refuses is named (`<uid> failed: ...`, no
values) and skipped; the others are sent and the command exits non-zero at the end.

On admindev the copies are counted per region (its README, "Client records"); a row read
straight from its database shows ciphertext in the names, email and mobile.

### Configuration snapshot

The admin site owns the configuration every region applies (epic-admin-interface entry 4,
AD-3 item 1, AD-22, interface I5; `integration.config`): the admin directory, who an
admin actor UUID is (deleted admin users stay listed, inactive), and the catalogue
(epic-catalogue entry 2; see "Catalogue" below). This portal pulls it
and keeps applying the last good one while the admin site is down. Admin users are never
created here.

| What | Where |
|------|-------|
| Pull | `pullconfig` job every 5 minutes (`manage.py pull_config`), plus a task on the worker after each signed `POST /api/v1/config/nudge` from the admin site (answered 202; nudges coalesce into one queued pull). A signed `GET <admin>/api/v1/config` to the only `INTERFACE_PEERS` entry (or `INTERFACE_ADMIN_SITE`) |
| Check | the snapshot's signature (admindev's key from `INTERFACE_PEER_KEYS`, `typ: config`, this site as the audience, the SHA-256 of the snapshot and its version) and its shape (mytp-core's `verify_snapshot`) before anything is applied |
| Apply | one transaction, the state row locked: a higher version replaces the directory (`integration_admindirectoryentry`) and every registered section (`register_section_applier`: the catalogue's `catalogue_*` tables; a snapshot without the section empties them) and becomes the applied one (`integration_configstate`); the same version changes nothing; an older one is refused (`stale`) |
| Refusals | the applied snapshot stays; `last_error` holds the code only: `network`, `http_<status>[_<code>]`, `bad_answer`, `bad_signature`, `audience`, `expired`, `invalid_body`, `newer_format` (the admin site needs a newer mytp-core: a warning line once per version), `stale`, `not_configured`, `signing_key` (the KMS could not unwrap the signing key), `apply_failed`; `pull_config` prints one line with the code and exits 0 (the outage warning below reports it once), except `not_configured` (no admin site, peer keys or signing key), which fails the job so its Sentry monitor shows it |
| Outage | no good pull for an hour: one Sentry warning "Configuration snapshot not pulled for an hour (<code>)" per outage, never the contents; the next good pull resets it |

Look at it (names of admin users are not patient data):

```sh
sudo -u mytp-dev ./venv/bin/python manage.py show_config    # version, applied at, last pull, last good pull, last error, the directory
sudo -u mytp-dev ./venv/bin/python manage.py pull_config    # pull_config: v<n> applied | unchanged, or pull_config: refused (<code>); ...
```

The `pullconfig` timer reaches a site only when `provision.sh` runs again after the
merge's deploy (see [Sites on the staging host](#sites-on-the-staging-host)); keep its
Sentry monitor `mytp-<site>-pullconfig` enabled for a while, as `dispatchoutbox`'s, or
disable it after its first run as for the other jobs. dev-ae pulls from admindev once its
keys are set up as dev's (see [Set up the keys](#set-up-the-keys-dev-and-admindev): its
own signing key, admindev's `add_peer_key ae ... --site dev-ae`, `INTERFACE_PEERS` and
`INTERFACE_PEER_KEYS` in dev-ae's settings.env).

Staging check: edit an admin user's display name on admindev; within seconds
`show_config` on dev and dev-ae shows the new name and the next version. Stop admindev's
Gunicorn and run `pull_config` on dev: it prints `refused (network)` or `refused (http_502)`, and
`show_config` still lists the directory; start it again, and the next pull is good.

### Catalogue

What a doctor is offered and at what net price (epic-catalogue entry 2, AD-3, AD-6, AD-16;
the `catalogue` app). The Commercial Manager defines products, country groups and
subscriptions on the admin site; they arrive in the configuration snapshot's `catalogue`
section (mytp-core 0.12.0) and are applied with it, in its transaction, into
`catalogue_countrygroup`, `catalogue_product` and `catalogue_subscription` (inactive rows
too). Nothing else writes these tables: there is no form or Django admin page for them, and
a row's `save()` or `delete()` raises. A snapshot without the section (an admin site
before 0.12.0) empties them, so nothing is offered.

`catalogue.resolver.offers(country, tier)` is the only code that prices: the active group
that lists the country (else the active catch-all, "Rest of world"), its active
subscription of that tier (none: no offers), then every active product at its net price,
less the subscription's discount for plain TPS (`tps`), rounded half up to cents. VAT,
vouchers, the billing company and checkout come with later entries; there is no portal
page yet.

```sh
sudo -u mytp-dev ./venv/bin/python manage.py price_catalogue --country ES --tier basic
#   ES basic (Iberia): 1 offer(s)
#     TPS  tps  90.00 EUR
sudo -u mytp-dev ./venv/bin/python manage.py price_catalogue --country US --tier basic
#   no offers for US basic
```

Staging check (after both deploys): as a Commercial Manager on admindev, create a TPS
product at 100.00 EUR and a basic Iberia subscription at 10 % (both in the audit log); on
dev, `pull_config` (or wait for the nudge), then the two commands above print 90.00 EUR
and nothing. Until the first catalogue (or staff user) change on admindev after its
deploy, the applied snapshot has no catalogue section and `price_catalogue` prints no
offers.

## Rollback

On the server as `debian`:

```sh
ls -1t /srv/mytp-dev/releases/          # newest first
readlink /srv/mytp-dev/current          # the live one
sudo -u mytp-dev ln -sfn /srv/mytp-dev/releases/<previous-sha> /srv/mytp-dev/current
sudo systemctl restart mytp-dev-gunicorn mytp-dev-worker
```

This does not reverse database migrations. The next push to `master` deploys over it.
To re-deploy a commit, re-run its `deploy-staging` job in GitHub Actions; this does not
work for commits built before the `deploy/release-ready` marker (epic-platform entry 10),
whose tarballs the release command refuses: use the `ln -sfn` above while the release is
still in `releases/`.

## Rotate the basic auth password

```sh
sudo htpasswd -B /etc/nginx/mytp-dev.htpasswd <htpasswd-user>   # prompts, not echoed
```

Nginx reads the file on each request; no reload needed. To add a person, run the same
command with a new user name; to remove one, `sudo htpasswd -D /etc/nginx/mytp-dev.htpasswd <user>`.

## Rotate the deploy key

Make a new key (step 5), replace the line in `/srv/mytp-dev/.ssh/authorized_keys`, and
update `STAGING_SSH_KEY` (dev-ae: `/srv/mytp-dev-ae/.ssh/authorized_keys` and
`STAGING_SSH_KEY_DEV_AE`). If the server is rebuilt, also refresh `STAGING_KNOWN_HOSTS`.

## Logs

```sh
sudo journalctl -u mytp-dev-gunicorn -n 200    # app (Gunicorn) logs
sudo journalctl -u 'mytp-dev-job@*' -n 200     # scheduled jobs
sudo journalctl -u mytp-dev-worker -n 200      # background tasks (worker)
sudo tail -f /var/log/nginx/error.log
```

Out of scope here: production (story 1.12); the field encryption key provider (story
2.12); email (story 1.5).
