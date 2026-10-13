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
| Off-region backups | nightly `backupdb` job → Object Storage SBG (`age`-encrypted); restore test: `restore-db.sh` |
| Field keys | `FIELD_ENCRYPTION_KEYS`, `BLIND_INDEX_KEY` in `shared/settings.env`; copy in the team password manager (step 12) |
| Keys (KMS) | OVHcloud KMS (France), staging service key; access certificate in `/srv/mytp/shared/kms-client.*`; daily `kmscheck` job |
| Errors and job alerts | Sentry (EU region), alerts to devops@mytreatplan.com |

Files in this folder:

- `provision.sh`: idempotent server setup (packages, SSH hardening, firewall, `mytp` user
  and directories, systemd units, scheduled job timers, sudoers, basic auth, Nginx,
  certificate).
- `release.sh`: installs one release; CI copies it to the server and runs it.
- `jobs.conf`: the scheduled jobs (name, `manage.py` command, schedule).
- `run-job.sh`: runs one job in the live release (started by `mytp-job@.service`).
- `restore-db.sh`: the restore test for the off-region backups (run on a team Mac).
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
   `/srv/mytp/shared/settings.env` (see `deploy/staging.env.example`). One `KEY=value`
   per line with **no comment after the value**: the settings loader keeps a trailing
   `# ...` as part of the value, and the upload then fails (`S3UploadFailedError`).

   ```sh
   sudo -u mytp nano /srv/mytp/shared/settings.env
   ```

   Then, from a copy of the merged `deploy/`, re-run
   `bash ~/deploy/provision.sh dev.mytpdso.com <htpasswd-user>`: it installs
   `postgresql-client` and `age`, creates `/srv/mytp/shared/backup-tmp` (0700, `mytp`)
   and the `mytp-job@backupdb.timer` (02:30 UTC). Check `pg_dump --version` says 17 (the
   managed service's major version; `backupdb` refuses an older client).
7. **First backup by hand.**

   ```sh
   sudo systemctl start mytp-job@backupdb           # waits until it finishes
   sudo journalctl -u mytp-job@backupdb -n 20       # "backupdb: uploaded staging/mytreatplan-...dump.age"
   ```

   The object appears in the SBG container. Downloaded, it starts with
   `age-encryption.org/v1` and is unreadable without the key. In Sentry, Crons now has
   `mytp-staging-backupdb` with an ok check-in: set its **Notify** to
   devops@mytreatplan.com (step 8.5).
8. **Alert check.** A run with wrong bucket credentials must fail and alert (the env var
   beats `settings.env`):

   ```sh
   cd /srv/mytp/current
   sudo -u mytp BACKUP_S3_SECRET_KEY=wrong ./venv/bin/python manage.py runjob backupdb
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
   sudo -u mytp sh -c 'umask 077 && openssl req -new -newkey rsa:3072 -nodes \
       -subj "/CN=mytp-kms-staging" \
       -keyout /srv/mytp/shared/kms-client.key -out /srv/mytp/shared/kms-client.csr'
   sudo cat /srv/mytp/shared/kms-client.csr
   ```

   In the KMS domain → *Generate an access certificate*: validity **365 days** (or the
   maximum offered), paste the CSR, and link **only** the identity `mytp-kms-staging`
   (never the account's root identity: it would bypass the IAM policy). Download the
   certificate (PEM) and copy it to the server:

   ```sh
   scp kms-client.crt debian@<instance-ip>:/tmp/kms-client.crt
   ssh debian@<instance-ip> 'sudo install -m 600 -o mytp -g mytp /tmp/kms-client.crt /srv/mytp/shared/kms-client.crt && rm /tmp/kms-client.crt && sudo rm /srv/mytp/shared/kms-client.csr'
   ```

   **No backup copy of the private key:** it is created on the server and never leaves
   it (not to a laptop, the password manager, chats or tickets). If it is lost, for
   example when the server is rebuilt, issue a new certificate ("Renew the access
   certificate" below); the data stays readable, because what protects it is the service
   key inside the KMS. In the team password manager, keep only a reference entry
   "MyTreatplan staging KMS" with the expiry date, domain id, endpoint and key id (none
   of them secret). Delete any local copy of the certificate. The credential files live
   only in `/srv/mytp/shared/` (owner `mytp`, mode 600), never in the repo.
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
5. **Settings.** Fill in the `KMS_*` keys in `/srv/mytp/shared/settings.env` (see
   `deploy/staging.env.example`; no comment after a value):

   ```sh
   sudo -u mytp nano /srv/mytp/shared/settings.env
   # KMS_ENDPOINT=https://eu-west-gra.okms.ovh.net
   # KMS_OKMS_ID=<domain id>
   # KMS_SERVICE_KEY_ID=<key id>
   # KMS_CLIENT_CERT=/srv/mytp/shared/kms-client.crt
   # KMS_CLIENT_KEY=/srv/mytp/shared/kms-client.key
   # KMS_CA_FILE stays empty (system CAs):
   # KMS_CA_FILE=
   sudo ls -l /srv/mytp/shared/kms-client.*     # -rw------- mytp mytp, both files
   ```

6. **Check by hand.**

   ```sh
   cd /srv/mytp/current
   sudo -u mytp ./venv/bin/python manage.py kmscheck
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
   `bash ~/deploy/provision.sh dev.mytpdso.com <htpasswd-user>`; it installs
   `mytp-job@kmscheck.timer` (04:00 UTC). Then:

   ```sh
   systemctl list-timers 'mytp-job@*'          # lists kmscheck
   sudo systemctl start mytp-job@kmscheck      # Sentry Crons: mytp-staging-kmscheck ok
   ```

   Set the monitor's **Notify** to devops@mytreatplan.com (step 8.5).

### 12. Field encryption keys (before merging story 2.2)

From story 2.2 the app encrypts personal fields (the users' email and names, the pending
registrations' email) with AES-256-GCM, and finds emails through a blind index
(HMAC-SHA256). It needs two keys in `/srv/mytp/shared/settings.env`:

- `FIELD_ENCRYPTION_KEYS`: `<key_id>:<base64 32-byte key>` entries, comma-separated. The
  first encrypts; the others only decrypt (during a rotation).
- `BLIND_INDEX_KEY`: its own base64 32-byte key.

Do this **before the merge**: the deploy runs `migrate`, which refuses to run without
valid keys (check errors `accounts.E001` / `accounts.E002`, naming the setting), and
whose data migration encrypts the existing users and pending registrations in place. A
deploy without the keys fails at `migrate` and the previous release stays live.

1. **Generate the keys on the server**, straight into the settings file, so they never
   show on a screen, a laptop or a chat (the release before 2.2 has no
   `generate_field_keys` yet; this is the same thing):

   ```sh
   sudo -u mytp sh -c 'umask 077
   k() { python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"; }
   printf "FIELD_ENCRYPTION_KEYS=k%s:%s\nBLIND_INDEX_KEY=%s\n" "$(date -u +%Y%m%dT%H%M)" "$(k)" "$(k)" \
       >> /srv/mytp/shared/settings.env'
   sudo grep -c -E '^(FIELD_ENCRYPTION_KEYS|BLIND_INDEX_KEY)=.' /srv/mytp/shared/settings.env   # 2
   sudo ls -l /srv/mytp/shared/settings.env                                                    # -rw------- mytp mytp
   ```

   If the file already has empty `FIELD_ENCRYPTION_KEYS=` / `BLIND_INDEX_KEY=` lines, delete
   them (`sudo -u mytp nano /srv/mytp/shared/settings.env`). Once 2.2 is live,
   `manage.py generate_field_keys` prints new keys the same way (to the terminal only).
2. **Copy them to the team password manager**, entry **"MyTreatplan staging field keys"**:
   open the file with `sudo -u mytp nano /srv/mytp/shared/settings.env`, copy the two
   lines into the entry, add the date and the key id (`k<UTC date>T<time>`, e.g. `k20261013T0812`), close without saving.
   Nowhere else: not the repo, a ticket, a chat, Sentry or a local file. Record that it
   was done in the pull request ("field keys in the password manager, <date>, <who>").
3. **Merge**, let the deploy run, then check:

   ```sh
   cd /srv/mytp/current
   sudo -u mytp ./venv/bin/python manage.py check                 # no issues
   sudo -u mytp ./venv/bin/python manage.py shell -c "from accounts.models import User; print(User.objects.by_email('<staging user email>').exists())"   # True
   ```

**Rolling back past 2.2 needs the migrations reversed first.** Once `migrate` has run
accounts 0003–0005 and signup 0002–0004, a pre-2.2 release reads ciphertext as emails and
names and cannot create users (it does not set `email_index`). This includes
`release.sh`'s automatic rollback after a failed health check. To go back, keep the keys
set and, **from the 2.2 release** (it has the reverse migrations), decrypt first, then
switch:

```sh
cd /srv/mytp/releases/<2.2 sha>
sudo -u mytp ./venv/bin/python manage.py migrate accounts 0002
sudo -u mytp ./venv/bin/python manage.py migrate signup 0001
# then point `current` at the older release and restart (see "Rollback")
```

If an automatic rollback already switched back, run the same two commands from the 2.2
release directory right away.

**Losing a key makes the data unreadable.** AES-256-GCM has no back door: without the
`FIELD_ENCRYPTION_KEYS` entry a value was encrypted with, nobody (us included) can read
that value again, and a lost key cannot be reissued. Without `BLIND_INDEX_KEY` no one can
log in or be found by email until every index is recomputed (which needs the encryption
key). The password manager copy is the only backup of the keys until the KMS provider
(story 2.12) wraps them.

**Backups need the same keys.** The nightly `backupdb` dumps (and OVHcloud's managed
backups) hold the ciphertext, not the plaintext. A restored database is readable only with
the `FIELD_ENCRYPTION_KEYS` entries its values were encrypted with, and logins work only
with the same `BLIND_INDEX_KEY`. So a key stays in the password manager for as long as any
backup encrypted with it may be restored (30 days of dumps after a rotation), and a
restore needs the keys as they were when the dump was taken.

**Rotate the encryption key** (e.g. yearly, or at once if a key may have leaked):

1. `cd /srv/mytp/current && sudo -u mytp ./venv/bin/python manage.py generate_field_keys --rotate`
   prints one new entry. Put it **first** in `FIELD_ENCRYPTION_KEYS`, keeping the current
   entries after it (comma-separated, no spaces), and add it to the password manager entry.
2. `sudo -u mytp ./venv/bin/python manage.py check` (no issues: the edited list parses),
   then `sudo systemctl restart mytp-gunicorn` (new values now use the new key; old ones
   still decrypt). A bad key would stop Gunicorn from starting.
3. `sudo -u mytp ./venv/bin/python manage.py reencrypt_fields` rewrites every encrypted
   value with the new key (and recomputes the blind indexes). It prints row counts and key
   ids only, and fails if any value is still on an older key.
4. Remove the old entry from `settings.env` only once no backup encrypted with it can
   still be needed (30 days); keep it in the password manager until then.

`BLIND_INDEX_KEY` is not rotated routinely. If it must change (leak), change it and run
`reencrypt_fields` right after the restart: until it finishes, email lookups and logins
fail.

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
   `/srv/mytp/shared/backup-tmp` (0700, `mytp`);
3. uploads it as `<BACKUP_PREFIX>mytreatplan-<yyyymmddThhmmssZ>.dump.age`, checks the object
   exists with the same size, and removes the temp file whatever happened.

It never deletes anything; old dumps expire through the lifecycle rule (step 10.3). Its key
cannot delete, but it can still overwrite an existing object (no versioning or object lock
yet; to decide for production, story 1.12). Its
error messages never contain the database password or the S3 keys.

```sh
sudo systemctl start mytp-job@backupdb          # back up now
sudo journalctl -u mytp-job@backupdb -n 50      # last runs
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
cd /srv/mytp/current
sudo -u mytp ./venv/bin/python manage.py shell -c "from django.db.migrations.recorder import MigrationRecorder; from django.contrib.auth import get_user_model; print(MigrationRecorder.Migration.objects.count(), get_user_model().objects.count())"
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

## Keys (KMS)

| What | Value |
|------|-------|
| KMS domain | OVHcloud KMS, region **GRA** (`eu-west-gra`), endpoint `https://eu-west-gra.okms.ovh.net`, domain id in `KMS_OKMS_ID` |
| Staging service key | `mytp-staging`: `oct`, 256 bits, wrapKey/unwrapKey/encrypt/decrypt, `SOFTWARE`; id in `KMS_SERVICE_KEY_ID` |
| Server credential | access certificate (mTLS) for IAM identity `mytp-kms-staging`, `/srv/mytp/shared/kms-client.crt` + `.key` (mode 600); expires 2027-10-12 17:07 UTC |
| IAM policy | `mytp-kms-staging-datakeys`: `serviceKey/get`, `serviceKey/dataKey/create`, `serviceKey/dataKey/decrypt` on this domain only |
| Backup of the credential | none (private key never leaves the server); if lost, issue a new certificate. Reference entry (no secrets) "MyTreatplan staging KMS" in the team password manager |
| Production | separate domain/key/certificate, story 1.12 (may use `HSM`) |

**Envelope encryption** (`encryption-policy.md`): the app asks the KMS for a data key
(`POST .../servicekey/<keyId>/datakey`), gets it both plain and wrapped by the service
key, encrypts with the plain one in memory and stores only the wrapped one next to the
data. To read, it sends the wrapped key to `POST .../datakey/decrypt`. The service key
never leaves the KMS. `mytp_publicsite/kms.py` is the client (standard library only);
story 2.12 builds the key provider on it.

**Daily check.** `kmscheck | kmscheck | *-*-* 04:00:00 UTC` in `jobs.conf`, so it runs via
`runjob` with Sentry check-ins. `manage.py kmscheck` gets the service key, generates a
256-bit data key, unwraps it and compares the bytes; it prints the key id and state, the
result and the certificate's expiry, and never the data key, the wrapped key or the
credential. It **fails 30 days before the certificate expires** (after still doing the
round trip), so Sentry alerts devops@mytreatplan.com in time to renew.

```sh
sudo systemctl start mytp-job@kmscheck          # check now
sudo journalctl -u mytp-job@kmscheck -n 50      # last runs
```

### Renew the access certificate

Do it when `kmscheck` reports "client certificate expires ..., within 30 days" (or
earlier). The new certificate goes in beside the old one, so nothing breaks meanwhile:

1. On the server, a new key and CSR (`-new` names, so the live files stay untouched):

   ```sh
   sudo -u mytp sh -c 'umask 077 && openssl req -new -newkey rsa:3072 -nodes \
       -subj "/CN=mytp-kms-staging" \
       -keyout /srv/mytp/shared/kms-client-new.key -out /srv/mytp/shared/kms-client-new.csr'
   sudo cat /srv/mytp/shared/kms-client-new.csr
   ```

2. KMS domain → *Generate an access certificate* with that CSR, validity 365 days,
   identity `mytp-kms-staging` only (the IAM policy applies to the identity, so it needs
   no change). Copy it to `/srv/mytp/shared/kms-client-new.crt` (`install -m 600 -o mytp
   -g mytp`, as in step 11.3).
3. Test the new pair without touching `settings.env` (env vars beat the file):

   ```sh
   cd /srv/mytp/current
   sudo -u mytp KMS_CLIENT_CERT=/srv/mytp/shared/kms-client-new.crt \
       KMS_CLIENT_KEY=/srv/mytp/shared/kms-client-new.key ./venv/bin/python manage.py kmscheck
   ```

4. When it prints "round trip ok" with the new expiry, swap the files in:

   ```sh
   sudo -u mytp mv /srv/mytp/shared/kms-client-new.crt /srv/mytp/shared/kms-client.crt
   sudo -u mytp mv /srv/mytp/shared/kms-client-new.key /srv/mytp/shared/kms-client.key
   sudo rm /srv/mytp/shared/kms-client-new.csr
   sudo systemctl restart mytp-gunicorn            # once the app uses the KMS (2.12)
   sudo systemctl start mytp-job@kmscheck
   ```

5. Update the expiry in the password manager reference entry and in the table above. Then delete the old certificate in the KMS panel (*Access certificates*),
   so the old private key no longer works anywhere.

If the private key may have leaked: do the same at once, and delete the old certificate
in the panel first.

### Rotate the service key

For story 2.12 to implement; until then no data is encrypted, so nothing needs
re-wrapping. Rotation changes which key wraps the data keys; it never re-encrypts the
data itself.

1. **New key.** In the KMS domain, generate a new service key with the same settings
   (`oct`, 256, wrapKey/unwrapKey/encrypt/decrypt, `SOFTWARE`), e.g. `mytp-staging-2`. The IAM policy
   covers the whole domain, so the server can use it at once.
2. **Switch.** Point `KMS_SERVICE_KEY_ID` in `/srv/mytp/shared/settings.env` at the new
   key id and `sudo systemctl restart mytp-gunicorn`. New data keys are now wrapped by the
   new key; `sudo systemctl start mytp-job@kmscheck` must show the new id, state active.
   Old wrapped keys still name (or are stored with) the old key id and still unwrap.
3. **Re-wrap old data keys.** For each stored wrapped data key that the old key wrapped:
   unwrap it with the old key (`datakey/decrypt` on the old key id), then wrap the same
   plaintext with the new key (`encrypt` on the new key id), and store the new wrapped
   key and the new key id in place of the old ones, in one transaction per record. The
   data encrypted with that data key is untouched. A key wrapped with `encrypt` is
   unwrapped with `decrypt` (not `datakey/decrypt`), so 2.12 must record how each data
   key was wrapped, and the IAM policy then also needs `okms:apikms:serviceKey/encrypt`
   and `okms:apikms:serviceKey/decrypt`. 2.12 provides this as a management command;
   until every record is done, both keys stay active.
4. **Deactivate the old key only once nothing uses it**: no stored record and no job
   refers to it any more, and every backup taken before step 3 has expired (the SBG
   dumps keep 30 days, so wait at least 30 days after step 3). Deactivate it in the
   panel, never from code. **Never delete it** while any data, including a backup,
   might still need it: a deleted key makes every data key it wrapped, and so that
   data, unreadable for good.
5. Update the table above (current key) and the password manager entry.

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

Out of scope here: production (story 1.12); the field encryption key provider (story
2.12); email (story 1.5).
