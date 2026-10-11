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

Files in this folder:

- `provision.sh`: idempotent server setup (packages, SSH hardening, firewall, `mytp` user
  and directories, systemd units, sudoers, basic auth, Nginx, certificate).
- `release.sh`: installs one release; CI copies it to the server and runs it.
- `nginx/mytp.conf.template`, `systemd/*`, `sudoers/mytp-deploy`: installed by `provision.sh`.
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
and `DATABASE_URL` (from step 2). Leave the other values as they are.

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
sudo tail -f /var/log/nginx/error.log
```

Out of scope here: production (story 1.12); backups, monitoring and cron jobs (story 1.9);
email (story 1.5).
