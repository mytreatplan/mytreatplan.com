#!/usr/bin/env bash
# Idempotent server setup for one site on a MyTreatplan staging host (Debian 13).
#
# Run on the server as the login user (debian), from a checkout or copy of the site's
# repo deploy/ (run it again after changing deploy/jobs.conf):
#
#     bash deploy/provision.sh <site> <domain> <htpasswd-user>
#     bash deploy/provision.sh dev dev.mytpdso.com <htpasswd-user>
#
# Host-wide (shared by every site): packages, the PostgreSQL client tools (from the
# PostgreSQL apt repository, PGDG, until Debian ships them), SSH hardening, firewall, the
# ACME webroot, certbot.timer, the release command /usr/local/bin/mytp-release
# (deploy/release.sh) and the virus scanner (ClamAV: clamd with deploy/clamav/clamd.conf,
# freshclam).
# Per site <site>: Linux user mytp-<site>, home /srv/mytp-<site>, sudoers rule
# /etc/sudoers.d/mytp-<site>-deploy, units mytp-<site>-gunicorn.{socket,service},
# mytp-<site>-worker.service (background tasks), mytp-<site>-scan-worker.service (virus
# scans of uploads; portal sites only, once their live release scans) and
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
#
# WSGI_MODULE: the site's WSGI module Gunicorn serves (default mytreatplan.wsgi, the
# portal). The admin site: WSGI_MODULE=mytp_admin.wsgi on every run for admindev.
#
# EXTRA_DOMAINS: space-separated redirect-only names (production EU site only, e.g.
# "mytreatplan.es www.mytreatplan.es mytreatplan.ie www.mytreatplan.ie"). Nginx answers
# them with a 301 to https://<domain>, path and query kept, and the site's certificate
# covers them. Not stored on the server: give it on every run; a run without it removes
# the redirect (with a warning).
#
# API_PEER_IPS: space-separated IPv4/IPv6 addresses or CIDR ranges allowed to reach /api/
# (production only: on a portal the admin site's egress addresses, on the admin site the
# portals'). Nginx then answers every other address on /api/ with 403; the rest of the
# site is unaffected. Not stored on the server: give it on every run; a run without it
# renders no allowlist (staging) and says so, but is refused (exit 2, before any change)
# on a site that has an allowlist installed. API_PEER_IPS=none removes an installed
# allowlist on purpose (with a warning).
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
WSGI_MODULE=${WSGI_MODULE:-mytreatplan.wsgi}
[[ $WSGI_MODULE =~ ^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$ ]] \
    || { echo "Invalid WSGI_MODULE: '$WSGI_MODULE' (a dotted Python module path)" >&2; exit 2; }
# EXTRA_DOMAINS: each name like the domain, not the domain itself, no duplicates.
# Normalised to lowercase names separated by one space ("" when unset).
extra_raw=$(printf '%s' "${EXTRA_DOMAINS:-}" | tr '[:upper:]' '[:lower:]')
domain_lc=$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]')
EXTRA_DOMAINS=""
set -f
for name in $extra_raw; do
    if [[ ! $name =~ ^[A-Za-z0-9.-]+$ || $name == "$domain_lc" || " $EXTRA_DOMAINS " == *" $name "* ]]; then
        set +f
        echo "Invalid EXTRA_DOMAINS: '${name}' (domain names, not $DOMAIN, no duplicates)" >&2
        exit 2
    fi
    EXTRA_DOMAINS=${EXTRA_DOMAINS:+$EXTRA_DOMAINS }$name
done
set +f
unset extra_raw domain_lc name
# API_PEER_IPS: each an IPv4 address (dotted quad, no leading zeros) or an IPv6 address
# (hex groups, at most one '::', no embedded IPv4), with an optional /prefix (8-32 or
# 16-128: a wider range is no allowlist), no duplicates, on one line. Normalised to lowercase values separated by one space ("" when
# unset).
valid_ipv4() {
    local IFS=. octet
    local -a octets
    [[ $1 =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || return 1
    read -ra octets <<<"$1"
    for octet in "${octets[@]}"; do
        [[ $octet =~ ^(0|[1-9][0-9]{0,2})$ ]] && ((octet <= 255)) || return 1
    done
}
valid_ipv6() {
    local addr=$1 head tail groups=0 group part
    local -a parts
    [[ $addr =~ ^[0-9a-f:]+$ && $addr != *:::* ]] || return 1
    if [[ $addr == *::* ]]; then
        head=${addr%%::*}
        tail=${addr#*::}
        [[ $tail != *::* ]] || return 1
    else
        head=$addr
        tail=""
    fi
    for part in "$head" "$tail"; do
        [[ -n $part ]] || continue
        IFS=: read -ra parts <<<"$part"
        [[ $part != :* && $part != *: ]] || return 1
        for group in "${parts[@]}"; do
            [[ $group =~ ^[0-9a-f]{1,4}$ ]] || return 1
            groups=$((groups + 1))
        done
    done
    if [[ $addr == *::* ]]; then
        ((groups <= 7))
    else
        ((groups == 8))
    fi
}
valid_peer_ip() {
    local addr=${1%/*} prefix=""
    if [[ $1 == */* ]]; then
        prefix=${1#*/}
        [[ $prefix =~ ^[1-9][0-9]{0,2}$ ]] || return 1
    fi
    if valid_ipv4 "$addr"; then
        [[ -z $prefix ]] || ((prefix >= 8 && prefix <= 32))
    elif valid_ipv6 "$addr"; then
        [[ -z $prefix ]] || ((prefix >= 16 && prefix <= 128))
    else
        return 1
    fi
}
if [[ ${API_PEER_IPS:-} == *[$'\n\r']* ]]; then
    echo "Invalid API_PEER_IPS: a line break (addresses separated by spaces, on one line)" >&2
    exit 2
fi
peers_raw=$(printf '%s' "${API_PEER_IPS:-}" | tr '[:upper:]' '[:lower:]')
API_PEER_IPS=""
# "none" alone (any case): no allowlist, said on purpose (API_PEER_NONE=1).
API_PEER_NONE=0
if [[ $peers_raw =~ ^[[:space:]]*none[[:space:]]*$ ]]; then
    API_PEER_NONE=1
    peers_raw=""
fi
set -f
for peer in $peers_raw; do
    if ! valid_peer_ip "$peer" || [[ " $API_PEER_IPS " == *" $peer "* ]]; then
        set +f
        echo "Invalid API_PEER_IPS: '${peer}' (IPv4 or IPv6 addresses or CIDR ranges," \
            "at most /8 or /16 wide, space-separated, no duplicates)" >&2
        exit 2
    fi
    API_PEER_IPS=${API_PEER_IPS:+$API_PEER_IPS }$peer
done
set +f
unset peers_raw peer
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
SCAN_WORKER=mytp-$SITE-scan-worker
RELEASE_COMMAND=/usr/local/bin/mytp-release
CERT_FILE=/etc/letsencrypt/live/$DOMAIN/fullchain.pem
PACKAGES=(nginx certbot python3-certbot-nginx python3-venv python3-pip gettext libpq5 ufw apache2-utils curl
    gpg age openssl clamav-daemon clamav-freshclam)
# The PostgreSQL client tools (pg_dump, pg_restore, psql), host-wide, installed after the
# PGDG source below: one explicit major, the newest of the sites' database services
# (staging: dev and dev-ae on 17, admindev on 18). A client serves servers of its major
# and older, and Debian's wrapper (/usr/bin/pg_dump) runs the newest major installed. A
# later major is added here deliberately, never pulled in by a metapackage.
PG_CLIENT_PACKAGES=(postgresql-client-18)
# The official PostgreSQL apt repository (PGDG), only while Debian has no
# postgresql-client-18 (its "PostgreSQL apt repository" step, removable on its own;
# deploy/README.md "PostgreSQL client tools" says how to drop it). Its signing key is
# pinned by fingerprint and checked before it is installed; apt trusts it for this
# source only (Signed-By). An apt preferences file keeps every other PGDG package below
# Debian's (priority 100): only the client, its common files and libpq5 come from PGDG.
# libpq5 is then PGDG's 18 for the whole host, the library psycopg (no binary extra)
# loads for every site's database connections, so its security fixes come from PGDG.
PGDG_KEY_URL=https://www.postgresql.org/media/keys/ACCC4CF8.asc
PGDG_KEY_FINGERPRINT=B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8
PGDG_KEY=/etc/apt/keyrings/mytp-pgdg.asc
PGDG_SOURCES=/etc/apt/sources.list.d/mytp-pgdg.sources
PGDG_PREFERENCES=/etc/apt/preferences.d/mytp-pgdg
PGDG_URI=https://apt.postgresql.org/pub/repos/apt
OS_RELEASE=/etc/os-release
# The virus scanner (host-wide, deploy/clamav/): clamd reads its own configuration file
# through a systemd drop-in; Debian's /etc/clamav/clamd.conf is left as it is.
CLAMD_CONF=/etc/clamav/clamd-mytp.conf
CLAMAV_DB_DIR=/var/lib/clamav
# clamd's spool for streamed files, inside its own directory (which Debian's AppArmor
# profile already lets it write); not /tmp, a RAM tmpfs on Debian 13.
CLAMD_TMP=/var/lib/clamav/tmp
# Debian enforces an AppArmor profile on clamd; its local include grants the portal's
# configuration file and spool (deploy/clamav/apparmor-usr.sbin.clamd).
CLAMD_APPARMOR_PROFILE=/etc/apparmor.d/usr.sbin.clamd
CLAMD_APPARMOR_LOCAL=/etc/apparmor.d/local/usr.sbin.clamd
CLAMD_SERVICE_DROPIN=/etc/systemd/system/clamav-daemon.service.d/mytp.conf
CLAMD_SOCKET_DROPIN=/etc/systemd/system/clamav-daemon.socket.d/mytp.conf

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

# --- /api/ allowlist guard --------------------------------------------------
# An installed /api/ allowlist is never dropped by leaving API_PEER_IPS out: the run
# stops here, before any change. API_PEER_IPS=none removes it on purpose. Installed:
# the allowlist section's marker, or, while the site is still HTTP-only (no
# certificate yet, so no /api/ block), the line render_nginx leaves in its place.
API_ALLOW_INSTALLED_RE='^# __API_ALLOW_(BEGIN|PENDING)__$'
if [[ -z $API_PEER_IPS ]] && ((!API_PEER_NONE)) && sudo test -f "$NGINX_SITE" \
    && sudo grep -qE "$API_ALLOW_INSTALLED_RE" "$NGINX_SITE"; then
    {
        echo "API_PEER_IPS is not set, but $NGINX_SITE limits /api/ to an allowlist."
        echo "Give API_PEER_IPS on every run for this site, or API_PEER_IPS=none to remove"
        echo "the allowlist on purpose. Nothing was changed."
    } >&2
    exit 2
fi

# install_packages PKG... -- installs those not installed yet, after one `apt-get update`
# per run (APT_UPDATED; a step that changes the apt sources updates again itself).
APT_UPDATED=0
install_packages() {
    local pkg missing=()
    for pkg in "$@"; do
        if [[ $(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null) != "install ok installed" ]]; then
            missing+=("$pkg")
        fi
    done
    ((${#missing[@]})) || return 0
    if ((!APT_UPDATED)); then
        sudo apt-get update -q
        APT_UPDATED=1
    fi
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${missing[@]}"
    changed "installed ${missing[*]}"
}

# --- Packages ---------------------------------------------------------------
step "Packages"
install_packages "${PACKAGES[@]}"

# --- PostgreSQL apt repository (PGDG) ------------------------------------------------
# Removable on its own once Debian ships postgresql-client-18 (deploy/README.md
# "PostgreSQL client tools"): delete this step and the PGDG_* settings, and remove the
# three files on each host; the package name stays and then comes from Debian.
# The key is fetched at every run (so a renewed expiry date arrives) and installed only
# when its one primary key has the pinned fingerprint, is neither expired nor revoked,
# and its content differs; the sources file (deb822, Signed-By that key) and the apt
# preferences only when they differ; `apt-get update` only when one of the three
# changed. Without a download the installed key is kept (a WARNING); a download that is
# not that key (another key, an expired or revoked one, or garbage) stops the run.
step "PostgreSQL apt repository (PGDG)"
# pgdg_key_status FILE -- prints `ok` when FILE holds exactly one primary key, the pinned
# one, valid; otherwise `expired`, `revoked` or `mismatch`.
pgdg_key_status() {
    local gnupg keys
    gnupg=$(mktemp -d "$WORK/gnupg.XXXXXX")
    keys=$(gpg --homedir "$gnupg" --batch --no-autostart --with-colons --show-keys "$1" \
        2>/dev/null | awk -F: '$1 == "pub" {validity = $2; primary = 1; next}
                               primary && $1 == "fpr" {print $10 ":" validity; primary = 0}') \
        || keys=""
    if [[ $keys != "$PGDG_KEY_FINGERPRINT":* || $keys == *$'\n'* ]]; then
        echo mismatch
    elif [[ ${keys#*:} == e ]]; then
        echo expired
    elif [[ ${keys#*:} == r ]]; then
        echo revoked
    else
        echo ok
    fi
}
pgdg_changed=0
pgdg_codename=$( (. "$OS_RELEASE" && printf '%s' "${VERSION_CODENAME:-}") 2>/dev/null || true)
[[ $pgdg_codename =~ ^[a-z]+$ ]] \
    || { echo "ERROR: no Debian codename in $OS_RELEASE for the PGDG source." >&2; exit 1; }
ensure_dir "$(dirname "$PGDG_KEY")" 0755 root:root
if curl -fsSL --proto '=https' --tlsv1.2 --retry 2 --max-time 60 -o "$WORK/pgdg.asc" "$PGDG_KEY_URL"; then
    pgdg_key=$(pgdg_key_status "$WORK/pgdg.asc")
    if [[ $pgdg_key == mismatch ]]; then
        echo "ERROR: the key at $PGDG_KEY_URL is not the pinned PGDG key" \
            "($PGDG_KEY_FINGERPRINT); nothing installed." >&2
        exit 1
    elif [[ $pgdg_key != ok ]]; then
        echo "ERROR: the PGDG key at $PGDG_KEY_URL ($PGDG_KEY_FINGERPRINT) is $pgdg_key;" \
            "nothing installed. Check https://www.postgresql.org/download/linux/debian/." >&2
        exit 1
    fi
    install_file "$WORK/pgdg.asc" "$PGDG_KEY" 0644 root:root && pgdg_changed=1
elif sudo test -f "$PGDG_KEY"; then
    echo "  WARNING: could not download $PGDG_KEY_URL; the installed key is kept." >&2
else
    echo "ERROR: could not download $PGDG_KEY_URL (the PGDG signing key); run this script again." >&2
    exit 1
fi
cat >"$WORK/pgdg.sources" <<EOF
# Installed by deploy/provision.sh: the PostgreSQL apt repository (PGDG), for
# ${PG_CLIENT_PACKAGES[*]} until Debian ships it (deploy/README.md "PostgreSQL client tools").
Types: deb
URIs: $PGDG_URI
Suites: $pgdg_codename-pgdg
Components: main
Architectures: $(dpkg --print-architecture)
Signed-By: $PGDG_KEY
EOF
install_file "$WORK/pgdg.sources" "$PGDG_SOURCES" 0644 root:root && pgdg_changed=1
cat >"$WORK/pgdg.pref" <<'EOF'
# Installed by deploy/provision.sh: nothing from the PostgreSQL apt repository (PGDG)
# unless asked for, except the client and what it needs from there.
Package: *
Pin: origin apt.postgresql.org
Pin-Priority: 100

Package: postgresql-client-18 postgresql-client-common libpq5
Pin: origin apt.postgresql.org
Pin-Priority: 500
EOF
install_file "$WORK/pgdg.pref" "$PGDG_PREFERENCES" 0644 root:root && pgdg_changed=1
if ((pgdg_changed)); then
    sudo apt-get update -q
    APT_UPDATED=1
fi

# --- PostgreSQL client tools (host-wide) ---------------------------------------------
step "PostgreSQL client tools"
install_packages "${PG_CLIENT_PACKAGES[@]}"

# --- Virus scanner (host-wide, AD-9) -----------------------------------------------
# ClamAV from Debian's packages (1.4 LTS line; decision 2026-11-07): clamd with the
# portal's configuration (deploy/clamav/clamd.conf: limits >= 1 GiB, a file it cannot
# fully scan is FOUND, socket /run/clamav/clamd.ctl for the group clamav) and freshclam
# keeping the signatures current. The portal sites' scan workers connect to it (see
# "Scan worker" below); deploy/README.md "Object storage" -> "Scanning".
step "Virus scanner (ClamAV)"
clamd_changed=0
ensure_dir "$CLAMD_TMP" 0700 clamav:clamav
ensure_dir "$(dirname "$CLAMD_SERVICE_DROPIN")" 0755 root:root
ensure_dir "$(dirname "$CLAMD_SOCKET_DROPIN")" 0755 root:root
ensure_dir "$(dirname "$CLAMD_APPARMOR_LOCAL")" 0755 root:root
install_file "$DEPLOY_DIR/clamav/clamd.conf" "$CLAMD_CONF" 0644 root:root && clamd_changed=1
install_file "$DEPLOY_DIR/clamav/clamav-daemon.service.conf" "$CLAMD_SERVICE_DROPIN" 0644 root:root \
    && clamd_changed=1
install_file "$DEPLOY_DIR/clamav/clamav-daemon.socket.conf" "$CLAMD_SOCKET_DROPIN" 0644 root:root \
    && clamd_changed=1
# The AppArmor rules take effect when the profile is reloaded: only when AppArmor runs
# and the local file changed.
if install_file "$DEPLOY_DIR/clamav/apparmor-usr.sbin.clamd" "$CLAMD_APPARMOR_LOCAL" 0644 root:root; then
    clamd_changed=1
    if systemctl is-active --quiet apparmor.service && sudo test -f "$CLAMD_APPARMOR_PROFILE"; then
        if sudo apparmor_parser -r "$CLAMD_APPARMOR_PROFILE"; then
            changed "AppArmor profile usr.sbin.clamd reloaded"
        else
            echo "  WARNING: the AppArmor profile usr.sbin.clamd did not reload; see: sudo journalctl -k | grep -i apparmor" >&2
        fi
    fi
fi
((clamd_changed)) && sudo systemctl daemon-reload
# clamd starts only once both signature databases exist (its unit's condition): on a new
# host, fetch them once now (freshclam's own service may be fetching them already, so it
# is stopped for that). A failure (e.g. the mirror's rate limit) warns; the next run
# tries again.
if ! compgen -G "$CLAMAV_DB_DIR/main.c[vl]d" >/dev/null \
    || ! compgen -G "$CLAMAV_DB_DIR/daily.c[vl]d" >/dev/null; then
    sudo systemctl stop clamav-freshclam.service 2>/dev/null || true
    if sudo freshclam --quiet; then
        changed "ClamAV signatures downloaded"
    else
        echo "  WARNING: freshclam could not download the signatures; run this script again." >&2
    fi
fi
for unit in clamav-freshclam.service clamav-daemon.service; do
    if ! systemctl is-enabled --quiet "$unit" 2>/dev/null; then
        sudo systemctl enable "$unit" >/dev/null 2>&1
        changed "$unit enabled"
    fi
done
if ! systemctl is-active --quiet clamav-freshclam.service; then
    sudo systemctl start clamav-freshclam.service
    changed "clamav-freshclam started"
fi
clamd_action=""
if ! systemctl is-active --quiet clamav-daemon.service; then
    clamd_action=start
elif ((clamd_changed)); then
    clamd_action=restart
fi
if [[ -n $clamd_action ]]; then
    # The socket unit (when Debian's clamd uses one) takes a new mode only when re-created.
    if ((clamd_changed)) && systemctl cat clamav-daemon.socket >/dev/null 2>&1; then
        sudo systemctl stop clamav-daemon.service clamav-daemon.socket
        sudo systemctl start clamav-daemon.socket
    fi
    # A start succeeds without running clamd when its condition (the signatures) is not
    # met, so it counts only once the unit is active. A clamd that cannot start must not
    # stop the remaining steps.
    if sudo systemctl "$clamd_action" clamav-daemon.service \
        && systemctl is-active --quiet clamav-daemon.service; then
        changed "clamav-daemon ${clamd_action}ed"
    else
        echo "  WARNING: clamav-daemon is not running after $clamd_action (no signatures yet, or a" \
            "configuration or AppArmor error); see: sudo journalctl -u clamav-daemon -n 50" >&2
    fi
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
# An older deploy/ copy never downgrades it: a release.sh whose RELEASE_TOOL_VERSION is
# lower than the installed one is skipped with a warning (the other steps still apply).
# release_tool_version FILE -- its RELEASE_TOOL_VERSION; 1 when it has none (before versioning).
release_tool_version() {
    local v
    v=$(sudo grep -m1 -E '^RELEASE_TOOL_VERSION=[0-9]+$' "$1" 2>/dev/null | cut -d= -f2 || true)
    echo "${v:-1}"
}
step "Release command"
new_release_version=$(release_tool_version "$DEPLOY_DIR/release.sh")
if sudo test -f "$RELEASE_COMMAND" \
    && (($(release_tool_version "$RELEASE_COMMAND") > new_release_version)); then
    echo "  WARNING: $RELEASE_COMMAND is version $(release_tool_version "$RELEASE_COMMAND"), newer than" \
        "this deploy/release.sh (version $new_release_version); not replaced. Use an up-to-date" \
        "copy of the portal's deploy/." >&2
else
    install_file "$DEPLOY_DIR/release.sh" "$RELEASE_COMMAND" 0755 root:root || true
fi

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
# manage.py update_geoip (the geoipupdate job) keeps the geo-IP country database here.
ensure_dir "$APP_HOME/shared/geoip" 0750 "$APP_USER:$APP_USER"
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
    sed -i "s/__WSGI_MODULE__/$WSGI_MODULE/g" "$WORK/gunicorn.$kind"
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

# --- Scan worker (epic-platform entry 12) -------------------------------------------
# mytp-<site>-scan-worker.service: the site's second worker, for the `scans` task backend
# only (the virus scans of uploads). It follows mytp-<site>-worker (WantedBy, PartOf), so
# release.sh's stop and restart of that worker apply to it too. Installed only when the
# live release scans uploads (the portal's storage/clamd.py; never on the admin site) and
# the main worker is installed; until then provision.sh must run again once it is. The
# site user joins the group clamav, which may open clamd's socket.
step "Scan worker ($SITE)"
if sudo test -f "$APP_HOME/current/storage/clamd.py" \
    && sudo test -f "/etc/systemd/system/$WORKER.service"; then
    if ! id -nG "$APP_USER" | tr ' ' '\n' | grep -qx clamav; then
        sudo usermod -aG clamav "$APP_USER"
        changed "$APP_USER added to the group clamav"
    fi
    render "$DEPLOY_DIR/systemd/scan-worker.service.template" "$WORK/scan-worker.service"
    scan_worker_changed=0
    if install_file "$WORK/scan-worker.service" "/etc/systemd/system/$SCAN_WORKER.service" 0644 root:root; then
        scan_worker_changed=1
        sudo systemctl daemon-reload
    fi
    if ! systemctl is-enabled --quiet "$SCAN_WORKER.service"; then
        sudo systemctl enable "$SCAN_WORKER.service" >/dev/null 2>&1
        changed "$SCAN_WORKER.service enabled"
    fi
    scan_worker_action=""
    if ! systemctl is-active --quiet "$SCAN_WORKER.service"; then
        scan_worker_action=start
    elif ((scan_worker_changed)); then
        scan_worker_action=restart
    fi
    if [[ -n $scan_worker_action ]]; then
        if sudo systemctl "$scan_worker_action" "$SCAN_WORKER.service"; then
            changed "$SCAN_WORKER.service ${scan_worker_action}ed"
        else
            echo "  WARNING: $SCAN_WORKER did not $scan_worker_action; see: sudo journalctl -u $SCAN_WORKER -n 50" >&2
        fi
    fi
else
    echo "  note: the live release does not scan uploads (or has no task worker); $SCAN_WORKER is not installed."
fi

# --- Scheduled jobs (deploy/jobs.conf) ----------------------------------------
# One mytp-<site>-job@<name>.timer per line of jobs.conf, firing
# mytp-<site>-job@<name>.service. This site's timers no longer listed are stopped and
# removed; other sites' timers are never looked at. A line may end with a fourth field,
# the sites it applies to (`| dev,dev-ae`, mytp-core 0.18.0): lines for other sites are
# skipped, so one job name can have a schedule per site (the weekly restore test).
step "Scheduled jobs ($SITE)"
jobs_changed=0
render "$DEPLOY_DIR/systemd/job@.service.template" "$WORK/job@.service"
if install_file "$WORK/job@.service" "/etc/systemd/system/$JOB@.service" 0644 root:root; then
    jobs_changed=1
fi
timer_template=$(sed "s/__SITE__/$SITE/g" "$DEPLOY_DIR/systemd/job.timer.template")
job_names=()
changed_timers=()
# A site name as mytp-core's jobs.SITE_RE (and this script's own site check).
site_re='^[a-z]([a-z0-9-]{0,18}[a-z0-9])?$'
while IFS= read -r line || [[ -n $line ]]; do
    IFS='|' read -r name _command schedule sites extra <<<"$line"
    name=$(trim "$name")
    [[ -z $name || $name == \#* ]] && continue
    schedule=$(trim "$schedule")
    [[ $name =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo "jobs.conf: invalid job name '$name'" >&2; exit 1; }
    [[ -n $schedule && -z $extra ]] || { echo "jobs.conf: expected 'name | command | schedule [| sites]' for '$name'" >&2; exit 1; }
    separators=${line//[^|]/}
    if ((${#separators} == 3)); then
        # The sites field: comma-separated site names, spaces around commas allowed; an
        # empty field or element, a bad or repeated name stops the run.
        sites=$(trim "$sites")
        [[ -n $sites && ! $sites =~ (^|,)[[:space:]]*(,|$) ]] \
            || { echo "jobs.conf: empty sites field or entry for '$name'" >&2; exit 1; }
        IFS=',' read -r -a site_list <<<"$sites"
        seen=,
        for entry in "${site_list[@]}"; do
            entry=$(trim "$entry")
            [[ $entry =~ $site_re ]] || { echo "jobs.conf: invalid site '$entry' for '$name'" >&2; exit 1; }
            [[ $seen != *",$entry,"* ]] || { echo "jobs.conf: site '$entry' listed twice for '$name'" >&2; exit 1; }
            seen+="$entry,"
        done
        [[ $seen == *",$SITE,"* ]] || continue   # another site's line
    fi
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
# cert_missing_names -- the EXTRA_DOMAINS the installed certificate does not cover, one
# per line (nothing when EXTRA_DOMAINS is unset or the certificate covers them all).
cert_missing_names() {
    [[ -n $EXTRA_DOMAINS ]] || return 0
    local sans name
    sans=$(sudo openssl x509 -noout -ext subjectAltName -in "$CERT_FILE" 2>/dev/null \
        | grep -o 'DNS:[^,[:space:]]*' | cut -d: -f2- | tr '[:upper:]' '[:lower:]' || true)
    for name in $EXTRA_DOMAINS; do
        grep -qxF "$name" <<<"$sans" || echo "$name"
    done
}

# render_nginx FULL(0|1): HTTP-only until the certificate exists. The EXTRA_DOMAINS
# answer ACME and redirect on port 80 always; their HTTPS redirect block is installed
# only once the certificate covers them all. API_PEER_IPS: one `allow` line each in
# /api/, then `deny all;`; unset, no allowlist (the marker section is dropped). The
# HTTP-only site has no /api/ block: with API_PEER_IPS it ends with the line
# `# __API_ALLOW_PENDING__`, so the allowlist guard still knows this site has one.
render_nginx() {
    local extra_sed=(-e 's/ __EXTRA_DOMAINS__//g')
    [[ -n $EXTRA_DOMAINS ]] && extra_sed=(-e "s/__EXTRA_DOMAINS__/$EXTRA_DOMAINS/g")
    sed "${extra_sed[@]}" -e "s/__DOMAIN__/$DOMAIN/g" -e "s/__SITE__/$SITE/g" \
        "$DEPLOY_DIR/nginx/site.conf.template" >"$WORK/site.conf"
    if [[ -n $API_PEER_IPS ]]; then
        awk -v peers="$API_PEER_IPS" '
            /^[ \t]*__API_ALLOW__$/ {
                indent = $0; sub(/__API_ALLOW__$/, "", indent)
                n = split(peers, list, " ")
                for (i = 1; i <= n; i++) print indent "allow " list[i] ";"
                next
            }
            { print }' "$WORK/site.conf" >"$WORK/site.conf.api"
        mv "$WORK/site.conf.api" "$WORK/site.conf"
    else
        sed -i.bak '/^# __API_ALLOW_BEGIN__$/,/^# __API_ALLOW_END__$/d' "$WORK/site.conf"
    fi
    if [[ $1 == 0 ]]; then
        sed -i.bak '/^# __HTTPS_BEGIN__$/,$d' "$WORK/site.conf"
        [[ -z $API_PEER_IPS ]] || echo '# __API_ALLOW_PENDING__' >>"$WORK/site.conf"
    elif [[ -z $EXTRA_DOMAINS || -n $(cert_missing_names) ]]; then
        sed -i.bak '/^# __EXTRA_BEGIN__$/,/^# __EXTRA_END__$/d' "$WORK/site.conf"
    fi
    rm -f "$WORK/site.conf.bak"
    if install_file "$WORK/site.conf" "$NGINX_SITE" 0644 root:root; then
        sudo nginx -t -q
        sudo systemctl reload nginx
    fi
}

step "Nginx ($SITE)"
if [[ -z $API_PEER_IPS ]]; then
    if ((API_PEER_NONE)); then
        echo "  /api/ has no IP allowlist (API_PEER_IPS=none)."
        if sudo test -f "$NGINX_SITE" \
            && sudo grep -qE "$API_ALLOW_INSTALLED_RE" "$NGINX_SITE"; then
            {
                echo "  WARNING: API_PEER_IPS=none, and $NGINX_SITE limits /api/ to an"
                echo "  allowlist; this run removes it."
            } >&2
        fi
    else
        echo "  /api/ has no IP allowlist (API_PEER_IPS not set; production sets it)."
    fi
fi
if [[ -z $EXTRA_DOMAINS ]] && sudo test -f "$NGINX_SITE" \
    && sudo grep -qE '^# __EXTRA_BEGIN__$|^[[:space:]]*server_name[[:space:]]+[^;[:space:]]+[[:space:]]+[^;[:space:]]' \
        "$NGINX_SITE"; then
    {
        echo "  WARNING: EXTRA_DOMAINS is not set, but $NGINX_SITE redirects extra names;"
        echo "  this run removes that redirect. Give EXTRA_DOMAINS on every run for this site."
    } >&2
fi
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
# One certificate (named after the domain) for the domain and the EXTRA_DOMAINS. Issued
# when missing; re-issued with --expand when it lacks an extra name. Every name must
# resolve to this host first, otherwise nothing is requested.
step "TLS certificate ($DOMAIN)"
cert_args=()
if ! sudo test -f "$CERT_FILE"; then
    cert_args=(certonly)
elif [[ -n $(cert_missing_names) ]]; then
    cert_args=(certonly --expand)
fi
if ((${#cert_args[@]})); then
    local_ips=$(ip -4 -o addr show scope global | awk '{split($4, a, "/"); print a[1]}')
    domain_args=()
    not_here=()
    for name in $DOMAIN $EXTRA_DOMAINS; do
        domain_args+=(-d "$name")
        resolved=$(getent ahostsv4 "$name" | awk '{print $1}' | sort -u || true)
        if [[ -z $resolved ]] || ! grep -qxF -f <(printf '%s\n' "$resolved") <<<"$local_ips"; then
            resolved=$(printf '%s' "$resolved" | tr '\n' ' ')
            not_here+=("$name does not resolve to this host (${resolved:-no A record})")
        fi
    done
    if ((${#not_here[@]} == 0)); then
        email_args=(--register-unsafely-without-email)
        [[ -n ${CERTBOT_EMAIL:-} ]] && email_args=(--email "$CERTBOT_EMAIL")
        # certonly: certbot leaves the Nginx config alone (this script owns it), and
        # the deploy hook reloads Nginx after each automatic renewal (certbot.timer).
        sudo certbot "${cert_args[@]}" --nginx --non-interactive --agree-tos "${email_args[@]}" \
            "${domain_args[@]}" --deploy-hook "systemctl reload nginx"
        changed "certificate for $DOMAIN${EXTRA_DOMAINS:+ $EXTRA_DOMAINS}"
        render_nginx 1
    else
        {
            for name in "${not_here[@]}"; do
                echo "  WARNING: $name;"
            done
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
