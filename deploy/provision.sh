#!/usr/bin/env bash
# Idempotent server setup for one site on a MyTreatplan staging host (Debian 13).
#
# Run on the server as the login user (debian), from a checkout or copy of the site's
# repo deploy/ (run it again after changing deploy/jobs.conf):
#
#     bash deploy/provision.sh <site> <domain> <htpasswd-user>
#     bash deploy/provision.sh dev dev.mytpdso.com <htpasswd-user>
#
# Host-wide (shared by every site): packages, SSH hardening, firewall, the ACME webroot,
# certbot.timer and the release command /usr/local/bin/mytp-release (deploy/release.sh).
# Per site <site>: Linux user mytp-<site>, home /srv/mytp-<site>, sudoers rule
# /etc/sudoers.d/mytp-<site>-deploy, units mytp-<site>-gunicorn.{socket,service},
# mytp-<site>-worker.service (background tasks) and
# mytp-<site>-job@<job>.{service,timer}, password file /etc/nginx/mytp-<site>.htpasswd,
# Nginx site mytp-<site>.conf and the domain's certificate. A run for one site never
# touches another site's files, units, timers or Nginx config.
#
# It uses sudo for every privileged step. The basic auth password is asked for only
# when <htpasswd-user> is not yet in the site's password file; it is never echoed or
# stored in plain text. A second run changes nothing and reports "No changes".
#
# Optional: CERTBOT_EMAIL=ops@example.com to register the Let's Encrypt account with
# an address (expiry notices). Without it the account is registered without email.
set -euo pipefail

SITE_RE='^[a-z]([a-z0-9-]{0,18}[a-z0-9])?$'

usage() {
    echo "Usage: $0 <site> <domain> <htpasswd-user>" >&2
    exit 2
}

[[ $# -eq 3 ]] || usage
SITE=$1
DOMAIN=$2
HTPASSWD_USER=$3
# The site name goes into user, path, unit and file names: lowercase letters, digits and
# '-', starting with a letter, not ending with '-', at most 20 characters.
[[ $SITE =~ $SITE_RE ]] || { echo "Invalid site name: '$SITE' (allowed: $SITE_RE)" >&2; exit 2; }
[[ $DOMAIN =~ ^[A-Za-z0-9.-]+$ ]] || { echo "Invalid domain: $DOMAIN" >&2; exit 2; }
[[ $HTPASSWD_USER =~ ^[A-Za-z0-9._-]+$ ]] || { echo "Invalid htpasswd user: $HTPASSWD_USER" >&2; exit 2; }
if [[ $EUID -eq 0 ]]; then
    echo "Run this as the login user (debian), not as root; it uses sudo itself." >&2
    exit 2
fi

DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APP_USER=mytp-$SITE
APP_HOME=/srv/mytp-$SITE
HTPASSWD_FILE=/etc/nginx/mytp-$SITE.htpasswd
NGINX_SITE=/etc/nginx/sites-available/mytp-$SITE.conf
NGINX_ENABLED=/etc/nginx/sites-enabled/mytp-$SITE.conf
SUDOERS_FILE=/etc/sudoers.d/mytp-$SITE-deploy
GUNICORN=mytp-$SITE-gunicorn
JOB=mytp-$SITE-job
WORKER=mytp-$SITE-worker
RELEASE_COMMAND=/usr/local/bin/mytp-release
CERT_FILE=/etc/letsencrypt/live/$DOMAIN/fullchain.pem
PACKAGES=(nginx certbot python3-certbot-nginx python3-venv python3-pip gettext libpq5 ufw apache2-utils curl
    postgresql-client age openssl)

CHANGES=0
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

trim() {
    local v=$1
    v=${v#"${v%%[![:space:]]*}"}
    printf '%s' "${v%"${v##*[![:space:]]}"}"
}
changed() { CHANGES=$((CHANGES + 1)); echo "  changed: $*"; }
step() { echo "==> $*"; }

# install_file SRC DEST MODE OWNER:GROUP -- installs only when content, mode or owner
# differ. Returns 0 when it changed something, 1 otherwise.
install_file() {
    local src=$1 dest=$2 mode=$3 owner=$4
    if sudo test -f "$dest" && sudo cmp -s "$src" "$dest" \
        && [[ $(sudo stat -c '%a %U:%G' "$dest") == "${mode#0} $owner" ]]; then
        return 1
    fi
    sudo install -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$src" "$dest"
    changed "$dest"
    return 0
}

# render TEMPLATE DEST -- the template with __SITE__ replaced by the site name.
render() {
    sed "s/__SITE__/$SITE/g" "$1" >"$2"
}

# ensure_dir PATH MODE OWNER:GROUP
ensure_dir() {
    local path=$1 mode=$2 owner=$3
    if sudo test -d "$path" && [[ $(sudo stat -c '%a %U:%G' "$path") == "${mode#0} $owner" ]]; then
        return 0
    fi
    sudo install -d -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$path"
    changed "$path/"
}

# --- Packages ---------------------------------------------------------------
step "Packages"
missing=()
for pkg in "${PACKAGES[@]}"; do
    if [[ $(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null) != "install ok installed" ]]; then
        missing+=("$pkg")
    fi
done
if ((${#missing[@]})); then
    sudo apt-get update -q
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${missing[@]}"
    changed "installed ${missing[*]}"
fi

# --- SSH: keys only, no root login -------------------------------------------
step "SSH hardening"
cat >"$WORK/ssh.conf" <<'EOF'
# Installed by deploy/provision.sh. Read before other drop-ins (first value wins).
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
if install_file "$WORK/ssh.conf" /etc/ssh/sshd_config.d/00-mytp-hardening.conf 0644 root:root; then
    sudo sshd -t
    sudo systemctl reload ssh
fi

# --- Firewall: 22, 80, 443 only ---------------------------------------------
step "Firewall"
ufw_status=$(sudo ufw status verbose)
for port in 22 80 443; do
    if ! grep -Eq "^${port}/tcp +ALLOW IN +Anywhere *$" <<<"$ufw_status"; then
        sudo ufw allow "${port}/tcp" >/dev/null
        changed "ufw allow ${port}/tcp"
    fi
done
if ! grep -q "Default: deny (incoming)" <<<"$ufw_status"; then
    sudo ufw default deny incoming >/dev/null
    changed "ufw default deny incoming"
fi
if ! grep -q "^Status: active" <<<"$ufw_status"; then
    sudo ufw --force enable >/dev/null
    changed "ufw enabled"
fi

# --- ACME webroot and certificate renewal (host-wide) --------------------------
step "Certificate renewal"
ensure_dir /var/www/letsencrypt 0755 root:root
if ! systemctl is-enabled --quiet certbot.timer 2>/dev/null; then
    sudo systemctl enable --now certbot.timer
    changed "certbot.timer enabled"
fi

# --- Release command (host-wide) --------------------------------------------------
# Every site's CI runs it as that site's user: mytp-release <site> <git-sha> <tarball>.
step "Release command"
install_file "$DEPLOY_DIR/release.sh" "$RELEASE_COMMAND" 0755 root:root || true

# --- App user and directories (site) ----------------------------------------------
step "App user and directories ($SITE)"
if ! id "$APP_USER" >/dev/null 2>&1; then
    sudo useradd --system --user-group --home-dir "$APP_HOME" --shell /bin/bash "$APP_USER"
    changed "user $APP_USER"
fi
# '*' = no usable password but not locked, so SSH key login works; never a password.
if [[ $(sudo getent shadow "$APP_USER" | cut -d: -f2) != '*' ]]; then
    sudo usermod -p '*' "$APP_USER"
    changed "$APP_USER password disabled"
fi
ensure_dir "$APP_HOME" 0755 "$APP_USER:$APP_USER"
ensure_dir "$APP_HOME/releases" 0755 "$APP_USER:$APP_USER"
ensure_dir "$APP_HOME/shared" 0750 "$APP_USER:$APP_USER"
ensure_dir "$APP_HOME/incoming" 0700 "$APP_USER:$APP_USER"
# manage.py backupdb writes the encrypted dump here before uploading it (story 1.14).
ensure_dir "$APP_HOME/shared/backup-tmp" 0700 "$APP_USER:$APP_USER"
ensure_dir "$APP_HOME/.ssh" 0700 "$APP_USER:$APP_USER"
if ! sudo test -f "$APP_HOME/.ssh/authorized_keys"; then
    sudo install -m 0600 -o "$APP_USER" -g "$APP_USER" /dev/null "$APP_HOME/.ssh/authorized_keys"
    changed "$APP_HOME/.ssh/authorized_keys (empty; add the deploy key, see deploy/README.md)"
fi

# --- sudoers: restart the site's app service and task worker only -----------------
step "sudoers ($SITE)"
render "$DEPLOY_DIR/sudoers/deploy.template" "$WORK/sudoers"
sudo visudo -cqf "$WORK/sudoers" >/dev/null
install_file "$WORK/sudoers" "$SUDOERS_FILE" 0440 root:root || true
sudo visudo -cq >/dev/null

# --- systemd units -----------------------------------------------------------
step "systemd units ($SITE)"
units_changed=0
for kind in socket service; do
    render "$DEPLOY_DIR/systemd/gunicorn.$kind.template" "$WORK/gunicorn.$kind"
    if install_file "$WORK/gunicorn.$kind" "/etc/systemd/system/$GUNICORN.$kind" 0644 root:root; then
        units_changed=1
    fi
done
if ((units_changed)); then
    sudo systemctl daemon-reload
fi
if ! systemctl is-enabled --quiet "$GUNICORN.socket"; then
    sudo systemctl enable "$GUNICORN.socket"
    changed "$GUNICORN.socket enabled"
fi
if ! systemctl is-active --quiet "$GUNICORN.socket"; then
    sudo systemctl start "$GUNICORN.socket"
    changed "$GUNICORN.socket started"
elif ((units_changed)); then
    # A socket cannot restart while its service runs; the service comes back on the
    # next request (socket activation).
    sudo systemctl stop "$GUNICORN.service" "$GUNICORN.socket"
    sudo systemctl start "$GUNICORN.socket"
fi

# --- Background task worker (AD-8) --------------------------------------------
# One mytp-<site>-worker.service per site: `manage.py db_worker` in the live release.
# deploy/release.sh stops it before `migrate` and starts it after every switch. It is
# installed, enabled and started only when the live release ships the worker
# (django-tasks-db in its requirements.txt); until then nothing is installed, release.sh
# skips the worker, and provision.sh must run again once such a release is live.
step "Task worker ($SITE)"
if sudo test -f "$APP_HOME/current/requirements.txt" \
    && sudo grep -qi '^django-tasks-db==' "$APP_HOME/current/requirements.txt"; then
    render "$DEPLOY_DIR/systemd/worker.service.template" "$WORK/worker.service"
    worker_changed=0
    if install_file "$WORK/worker.service" "/etc/systemd/system/$WORKER.service" 0644 root:root; then
        worker_changed=1
        sudo systemctl daemon-reload
    fi
    if ! systemctl is-enabled --quiet "$WORKER.service"; then
        sudo systemctl enable "$WORKER.service" >/dev/null 2>&1
        changed "$WORKER.service enabled"
    fi
    worker_action=""
    if ! systemctl is-active --quiet "$WORKER.service"; then
        worker_action=start
    elif ((worker_changed)); then
        worker_action=restart
    fi
    if [[ -n $worker_action ]]; then
        # A worker that cannot start must not stop the remaining steps (jobs, Nginx, TLS).
        if sudo systemctl "$worker_action" "$WORKER.service"; then
            changed "$WORKER.service ${worker_action}ed"
        else
            echo "  WARNING: $WORKER did not $worker_action; see: sudo journalctl -u $WORKER -n 50" >&2
        fi
    fi
else
    echo "  note: the live release has no task worker yet; $WORKER is not installed."
    echo "  Run this script again once a release with django-tasks-db is live."
fi

# --- Scheduled jobs (deploy/jobs.conf) ----------------------------------------
# One mytp-<site>-job@<name>.timer per line of jobs.conf, firing
# mytp-<site>-job@<name>.service. This site's timers no longer listed are stopped and
# removed; other sites' timers are never looked at.
step "Scheduled jobs ($SITE)"
jobs_changed=0
render "$DEPLOY_DIR/systemd/job@.service.template" "$WORK/job@.service"
if install_file "$WORK/job@.service" "/etc/systemd/system/$JOB@.service" 0644 root:root; then
    jobs_changed=1
fi
timer_template=$(sed "s/__SITE__/$SITE/g" "$DEPLOY_DIR/systemd/job.timer.template")
job_names=()
changed_timers=()
while IFS='|' read -r name _command schedule extra || [[ -n $name ]]; do
    name=$(trim "$name")
    [[ -z $name || $name == \#* ]] && continue
    schedule=$(trim "$schedule")
    [[ $name =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo "jobs.conf: invalid job name '$name'" >&2; exit 1; }
    [[ -n $schedule && -z $extra ]] || { echo "jobs.conf: expected 'name | command | schedule' for '$name'" >&2; exit 1; }
    for existing in "${job_names[@]}"; do
        [[ $existing != "$name" ]] || { echo "jobs.conf: duplicate job '$name'" >&2; exit 1; }
    done
    job_names+=("$name")
    timer=${timer_template//__NAME__/"$name"}
    timer=${timer//__SCHEDULE__/"$schedule"}
    printf '%s\n' "$timer" >"$WORK/$JOB@$name.timer"
    if install_file "$WORK/$JOB@$name.timer" "/etc/systemd/system/$JOB@$name.timer" 0644 root:root; then
        jobs_changed=1
        changed_timers+=("$name")
    fi
done <"$DEPLOY_DIR/jobs.conf"
# "$JOB@" ends in '@', and site names have no '@', so this matches only this site's
# timers (mytp-dev-job@* never matches mytp-dev-ae-job@*).
for path in "/etc/systemd/system/$JOB@"*.timer; do
    [[ -e $path ]] || continue
    name=${path#"/etc/systemd/system/$JOB@"}
    name=${name%.timer}
    listed=0
    for wanted in "${job_names[@]}"; do
        [[ $wanted == "$name" ]] && listed=1
    done
    if ((!listed)); then
        sudo systemctl disable --now "$JOB@$name.timer" >/dev/null 2>&1 || true
        sudo rm -f "$path"
        jobs_changed=1
        changed "removed $JOB@$name.timer"
    fi
done
if ((jobs_changed)); then
    sudo systemctl daemon-reload
fi
# The jobs.conf these timers came from; `manage.py runjob` takes the Sentry monitor
# schedules from it, and release.sh warns when a release's copy differs.
install_file "$DEPLOY_DIR/jobs.conf" "$APP_HOME/shared/jobs.conf" 0644 "$APP_USER:$APP_USER" || true
for name in "${job_names[@]}"; do
    timer=$JOB@$name.timer
    if ! systemctl is-enabled --quiet "$timer"; then
        sudo systemctl enable "$timer" >/dev/null 2>&1
        changed "$timer enabled"
    fi
    if ! systemctl is-active --quiet "$timer"; then
        sudo systemctl start "$timer"
        changed "$timer started"
    elif [[ " ${changed_timers[*]} " == *" $name "* ]]; then
        # Pick up a new schedule.
        sudo systemctl restart "$timer"
    fi
done

# --- Basic auth --------------------------------------------------------------
step "Basic auth ($SITE)"
if ! sudo test -f "$HTPASSWD_FILE" || ! sudo cut -d: -f1 "$HTPASSWD_FILE" | grep -qxF "$HTPASSWD_USER"; then
    read -rsp "Basic auth password for '$HTPASSWD_USER': " password; echo
    read -rsp "Repeat password: " password2; echo
    if [[ -z $password || $password != "$password2" ]]; then
        echo "Passwords are empty or do not match." >&2
        exit 1
    fi
    create_flag=()
    sudo test -f "$HTPASSWD_FILE" || create_flag=(-c)
    printf '%s\n' "$password" | sudo htpasswd -i -B "${create_flag[@]}" "$HTPASSWD_FILE" "$HTPASSWD_USER"
    unset password password2
    changed "$HTPASSWD_FILE ($HTPASSWD_USER)"
fi
if [[ $(sudo stat -c '%a %U:%G' "$HTPASSWD_FILE") != "640 root:www-data" ]]; then
    sudo chown root:www-data "$HTPASSWD_FILE"
    sudo chmod 0640 "$HTPASSWD_FILE"
    changed "$HTPASSWD_FILE permissions"
fi

# --- Nginx site --------------------------------------------------------------
# render_nginx FULL(0|1): HTTP-only until the certificate exists.
render_nginx() {
    sed -e "s/__DOMAIN__/$DOMAIN/g" -e "s/__SITE__/$SITE/g" "$DEPLOY_DIR/nginx/site.conf.template" \
        >"$WORK/site.conf"
    if [[ $1 == 0 ]]; then
        sed -i '/^# __HTTPS_BEGIN__/,$d' "$WORK/site.conf"
    fi
    if install_file "$WORK/site.conf" "$NGINX_SITE" 0644 root:root; then
        sudo nginx -t -q
        sudo systemctl reload nginx
    fi
}

step "Nginx ($SITE)"
# Host-wide: the Debian default site would answer for unknown names.
if [[ -L /etc/nginx/sites-enabled/default ]]; then
    sudo rm /etc/nginx/sites-enabled/default
    changed "removed default Nginx site"
fi
if [[ $(readlink "$NGINX_ENABLED" || true) != "$NGINX_SITE" ]]; then
    # Install the site file first so the link never dangles.
    sudo test -f "$NGINX_SITE" || render_nginx 0
    sudo ln -sfn "$NGINX_SITE" "$NGINX_ENABLED"
    sudo nginx -t -q
    sudo systemctl reload nginx
    changed "enabled Nginx site"
fi
if sudo test -f "$CERT_FILE"; then
    render_nginx 1
else
    render_nginx 0
fi
if ! systemctl is-active --quiet nginx; then
    sudo systemctl start nginx
    changed "nginx started"
fi

# --- TLS certificate (Let's Encrypt) -----------------------------------------
step "TLS certificate ($DOMAIN)"
if ! sudo test -f "$CERT_FILE"; then
    resolved=$(getent ahostsv4 "$DOMAIN" | awk '{print $1}' | sort -u || true)
    local_ips=$(ip -4 -o addr show scope global | awk '{split($4, a, "/"); print a[1]}')
    if [[ -n $resolved ]] && grep -qxF -f <(printf '%s\n' "$resolved") <<<"$local_ips"; then
        email_args=(--register-unsafely-without-email)
        [[ -n ${CERTBOT_EMAIL:-} ]] && email_args=(--email "$CERTBOT_EMAIL")
        # certonly: certbot leaves the Nginx config alone (this script owns it), and
        # the deploy hook reloads Nginx after each automatic renewal (certbot.timer).
        sudo certbot certonly --nginx --non-interactive --agree-tos "${email_args[@]}" \
            -d "$DOMAIN" --deploy-hook "systemctl reload nginx"
        changed "certificate for $DOMAIN"
        render_nginx 1
    else
        {
            echo "  WARNING: $DOMAIN does not resolve to this host (${resolved:-no A record});"
            echo "  skipping the certificate. Fix DNS and run this script again."
        } >&2
    fi
fi

echo
if ((CHANGES)); then
    echo "Done: $CHANGES change(s)."
else
    echo "No changes."
fi
if ! sudo test -f "$APP_HOME/shared/settings.env"; then
    echo "Next: create $APP_HOME/shared/settings.env from deploy/staging.env.example, with SITE=$SITE"
    echo "(see deploy/README.md)."
fi
