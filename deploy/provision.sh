#!/usr/bin/env bash
# Idempotent server setup for the MyTreatplan staging host (Debian 13).
#
# Run on the server as the login user (debian), from a checkout or copy of deploy/
# (run it again after changing deploy/jobs.conf):
#
#     bash deploy/provision.sh dev.mytpdso.com <htpasswd-user>
#
# It uses sudo for every privileged step. The basic auth password is asked for only
# when <htpasswd-user> is not yet in the password file; it is never echoed or stored
# in plain text. A second run changes nothing and reports "No changes".
#
# Optional: CERTBOT_EMAIL=ops@example.com to register the Let's Encrypt account with
# an address (expiry notices). Without it the account is registered without email.
set -euo pipefail

usage() {
    echo "Usage: $0 <domain> <htpasswd-user>" >&2
    exit 2
}

[[ $# -eq 2 ]] || usage
DOMAIN=$1
HTPASSWD_USER=$2
[[ $DOMAIN =~ ^[A-Za-z0-9.-]+$ ]] || { echo "Invalid domain: $DOMAIN" >&2; exit 2; }
[[ $HTPASSWD_USER =~ ^[A-Za-z0-9._-]+$ ]] || { echo "Invalid htpasswd user: $HTPASSWD_USER" >&2; exit 2; }
if [[ $EUID -eq 0 ]]; then
    echo "Run this as the login user (debian), not as root; it uses sudo itself." >&2
    exit 2
fi

DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APP_USER=mytp
APP_HOME=/srv/mytp
HTPASSWD_FILE=/etc/nginx/mytp.htpasswd
NGINX_SITE=/etc/nginx/sites-available/mytp.conf
CERT_FILE=/etc/letsencrypt/live/$DOMAIN/fullchain.pem
PACKAGES=(nginx certbot python3-certbot-nginx python3-venv python3-pip gettext libpq5 ufw apache2-utils curl
    postgresql-client age)

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

# --- App user and directories -----------------------------------------------
step "App user and directories"
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
ensure_dir /var/www/letsencrypt 0755 root:root

# --- sudoers: restart the app service only -----------------------------------
step "sudoers"
sudo visudo -cqf "$DEPLOY_DIR/sudoers/mytp-deploy" >/dev/null
install_file "$DEPLOY_DIR/sudoers/mytp-deploy" /etc/sudoers.d/mytp-deploy 0440 root:root || true
sudo visudo -cq >/dev/null

# --- systemd units -----------------------------------------------------------
step "systemd units"
units_changed=0
for unit in mytp-gunicorn.socket mytp-gunicorn.service; do
    if install_file "$DEPLOY_DIR/systemd/$unit" "/etc/systemd/system/$unit" 0644 root:root; then
        units_changed=1
    fi
done
if ((units_changed)); then
    sudo systemctl daemon-reload
fi
if ! systemctl is-enabled --quiet mytp-gunicorn.socket; then
    sudo systemctl enable mytp-gunicorn.socket
    changed "mytp-gunicorn.socket enabled"
fi
if ! systemctl is-active --quiet mytp-gunicorn.socket; then
    sudo systemctl start mytp-gunicorn.socket
    changed "mytp-gunicorn.socket started"
elif ((units_changed)); then
    # A socket cannot restart while its service runs; the service comes back on the
    # next request (socket activation).
    sudo systemctl stop mytp-gunicorn.service mytp-gunicorn.socket
    sudo systemctl start mytp-gunicorn.socket
fi

# --- Scheduled jobs (deploy/jobs.conf) ----------------------------------------
# One mytp-job@<name>.timer per line of jobs.conf, firing mytp-job@<name>.service.
# Timers no longer listed are stopped and removed.
step "Scheduled jobs"
jobs_changed=0
if install_file "$DEPLOY_DIR/systemd/mytp-job@.service" /etc/systemd/system/mytp-job@.service 0644 root:root; then
    jobs_changed=1
fi
timer_template=$(<"$DEPLOY_DIR/systemd/mytp-job.timer.template")
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
    printf '%s\n' "$timer" >"$WORK/mytp-job@$name.timer"
    if install_file "$WORK/mytp-job@$name.timer" "/etc/systemd/system/mytp-job@$name.timer" 0644 root:root; then
        jobs_changed=1
        changed_timers+=("$name")
    fi
done <"$DEPLOY_DIR/jobs.conf"
for path in /etc/systemd/system/mytp-job@*.timer; do
    [[ -e $path ]] || continue
    name=${path#/etc/systemd/system/mytp-job@}
    name=${name%.timer}
    listed=0
    for wanted in "${job_names[@]}"; do
        [[ $wanted == "$name" ]] && listed=1
    done
    if ((!listed)); then
        sudo systemctl disable --now "mytp-job@$name.timer" >/dev/null 2>&1 || true
        sudo rm -f "$path"
        jobs_changed=1
        changed "removed mytp-job@$name.timer"
    fi
done
if ((jobs_changed)); then
    sudo systemctl daemon-reload
fi
# The jobs.conf these timers came from; `manage.py runjob` takes the Sentry monitor
# schedules from it, and release.sh warns when a release's copy differs.
install_file "$DEPLOY_DIR/jobs.conf" "$APP_HOME/shared/jobs.conf" 0644 "$APP_USER:$APP_USER" || true
for name in "${job_names[@]}"; do
    timer=mytp-job@$name.timer
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
step "Basic auth"
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
    sed "s/__DOMAIN__/$DOMAIN/g" "$DEPLOY_DIR/nginx/mytp.conf.template" >"$WORK/mytp.conf"
    if [[ $1 == 0 ]]; then
        sed -i '/^# __HTTPS_BEGIN__/,$d' "$WORK/mytp.conf"
    fi
    if install_file "$WORK/mytp.conf" "$NGINX_SITE" 0644 root:root; then
        sudo nginx -t -q
        sudo systemctl reload nginx
    fi
}

step "Nginx"
if [[ -L /etc/nginx/sites-enabled/default ]]; then
    sudo rm /etc/nginx/sites-enabled/default
    changed "removed default Nginx site"
fi
if [[ $(readlink /etc/nginx/sites-enabled/mytp.conf || true) != "$NGINX_SITE" ]]; then
    # Install the site file first so the link never dangles.
    sudo test -f "$NGINX_SITE" || render_nginx 0
    sudo ln -sfn "$NGINX_SITE" /etc/nginx/sites-enabled/mytp.conf
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
step "TLS certificate"
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
if ! systemctl is-enabled --quiet certbot.timer 2>/dev/null; then
    sudo systemctl enable --now certbot.timer
    changed "certbot.timer enabled"
fi

echo
if ((CHANGES)); then
    echo "Done: $CHANGES change(s)."
else
    echo "No changes."
fi
if ! sudo test -f "$APP_HOME/shared/settings.env"; then
    echo "Next: create $APP_HOME/shared/settings.env from deploy/staging.env.example (see deploy/README.md)."
fi
