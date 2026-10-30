#!/usr/bin/env bash
# Installs one release of one site on the server and makes it live only if every step
# succeeds. provision.sh installs it host-wide as /usr/local/bin/mytp-release, so any
# repo's CI can deploy to its site; it runs as that site's user (mytp-<site>):
#
#     mytp-release <site> <git-sha> <tarball>
#     mytp-release dev 0123abc /srv/mytp-dev/incoming/0123abc.tar.gz
#
# The site must be provisioned (/srv/mytp-<site> exists) and its settings.env must say
# SITE=<site>.
#
# Flow: unpack to releases/<sha> (with a REVISION file holding the SHA), build its
# venv, compilemessages, collectstatic, migrate, createcachetable. Only then switch `current`, restart Gunicorn and check
# health through the socket. If the check fails, switch back, restart, exit non-zero.
# Keeps the last 5 releases.
set -euo pipefail

SITE_RE='^[a-z]([a-z0-9-]{0,18}[a-z0-9])?$'
KEEP_RELEASES=5
HEALTH_PATH=/robots.txt
HEALTH_TRIES=15

log() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

[[ $# -eq 3 ]] || die "usage: $0 <site> <git-sha> <tarball>"
SITE=$1
SHA=$2
TARBALL=$3
[[ $SITE =~ $SITE_RE ]] || die "invalid site name: '$SITE' (allowed: $SITE_RE)"
[[ $SHA =~ ^[0-9a-f]{7,40}$ ]] || die "not a git sha: $SHA"

APP_HOME=/srv/mytp-$SITE
APP_USER=mytp-$SITE
SOCKET=/run/mytp-$SITE-gunicorn.sock
SERVICE=mytp-$SITE-gunicorn
[[ -d $APP_HOME ]] || die "site '$SITE' is not provisioned: $APP_HOME is missing (run deploy/provision.sh)"
[[ $(id -un) == "$APP_USER" ]] || die "run this as $APP_USER, not $(id -un)"
[[ -f $TARBALL ]] || die "tarball not found: $TARBALL"

RELEASES=$APP_HOME/releases
SHARED=$APP_HOME/shared
CURRENT=$APP_HOME/current
RELEASE=$RELEASES/$SHA

# The tarball is a one-off upload.
cleanup() { rm -f "$TARBALL"; }
trap cleanup EXIT

# One release at a time per site (CI also serialises deploys).
exec 9>"$APP_HOME/.release.lock"
flock -w 600 9 || die "another release is still running"

[[ -f $SHARED/settings.env ]] || die "$SHARED/settings.env is missing (see deploy/README.md)"

# The value of KEY in settings.env (last line wins), without quotes or spaces.
env_value() {
    grep -E "^$1=" "$SHARED/settings.env" | tail -n1 | cut -d= -f2- | tr -d "\"' "
}
# The app derives its paths from SITE; a copied settings.env must not point at another site.
env_site=$(env_value SITE || true)
[[ $env_site == "$SITE" ]] || die "SITE in $SHARED/settings.env is '${env_site}', expected '$SITE'"

# Health check: a request through the Gunicorn socket, as Nginx would send it.
site_host() {
    local hosts
    hosts=$(env_value ALLOWED_HOSTS || true)
    echo "${hosts%%,*}"
}
healthy() {
    local host status i
    host=$(site_host)
    [[ -n $host ]] || { echo "ALLOWED_HOSTS is empty in settings.env" >&2; return 1; }
    for ((i = 1; i <= HEALTH_TRIES; i++)); do
        status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 --unix-socket "$SOCKET" \
            -H "Host: $host" -H 'X-Forwarded-Proto: https' "http://localhost$HEALTH_PATH" || true)
        if [[ $status == 200 ]]; then
            echo "health check passed ($HEALTH_PATH -> 200)"
            return 0
        fi
        echo "health check attempt $i/$HEALTH_TRIES: HTTP $status"
        sleep 2
    done
    return 1
}
restart_app() { sudo -n /usr/bin/systemctl restart "$SERVICE"; }
# Atomic switch of the `current` symlink.
point_current_at() {
    ln -sfn "$1" "$CURRENT.tmp"
    mv -T "$CURRENT.tmp" "$CURRENT"
}

PREVIOUS=$(readlink -f "$CURRENT" 2>/dev/null || true)
[[ -d $PREVIOUS ]] || PREVIOUS=""

if [[ $PREVIOUS == "$RELEASE" ]]; then
    log "$SHA is already the live release; checking health only"
    healthy || die "live release $SHA is not healthy"
    exit 0
fi

# --- Build (nothing live changes until the switch) ---------------------------
# A leftover from an earlier failed attempt of the same commit is rebuilt from scratch.
rm -rf "$RELEASE"
# Remove the half-built release if any step fails before the switch.
switched=0
on_build_error() {
    if ((!switched)); then
        echo "ERROR: release $SHA failed before the switch; current release unchanged." >&2
        rm -rf "$RELEASE"
    fi
}
trap 'on_build_error; cleanup' EXIT

log "Unpacking $SHA"
mkdir -p "$RELEASE"
tar -xzf "$TARBALL" -C "$RELEASE"
[[ -f $RELEASE/static/css/site.css ]] || die "tarball has no built static/css/site.css"
ln -s "$SHARED/settings.env" "$RELEASE/settings.env"
# The git SHA, read by settings as Sentry's `release` (RELEASE_SHA in the env wins).
echo "$SHA" >"$RELEASE/REVISION"
# Timers (and Sentry monitor schedules) follow the provisioned jobs.conf, not the release's.
if ! cmp -s "$RELEASE/deploy/jobs.conf" "$SHARED/jobs.conf"; then
    echo "WARNING: deploy/jobs.conf in this release differs from $SHARED/jobs.conf (or that is" >&2
    echo "WARNING: missing). Scheduled jobs keep the old timers until you re-run deploy/provision.sh." >&2
fi

log "Building venv"
python3 -m venv "$RELEASE/venv"
# From the release folder: requirements.txt names the vendored mytp-core wheel by a path
# relative to it (./vendor/...), and pip resolves such paths from the working directory.
(cd "$RELEASE" && ./venv/bin/pip install --quiet --disable-pip-version-check --no-cache-dir \
    -r requirements.txt </dev/null)

manage() { (cd "$RELEASE" && ./venv/bin/python manage.py "$@" </dev/null); }
log "compilemessages"
manage compilemessages --ignore venv
log "collectstatic"
manage collectstatic --noinput --verbosity 0
log "migrate"
manage migrate --noinput
log "createcachetable"
manage createcachetable

# --- Switch ------------------------------------------------------------------
log "Switching current -> $SHA"
switched=1
touch "$RELEASE"
point_current_at "$RELEASE"
restart_status=0
restart_app || restart_status=$?

if ((restart_status == 0)) && healthy; then
    log "Release $SHA is live"
else
    echo "ERROR: release $SHA is not healthy after restart." >&2
    if [[ -n $PREVIOUS ]]; then
        echo "Rolling back to $(basename "$PREVIOUS")" >&2
        point_current_at "$PREVIOUS"
        restart_app || true
        healthy || echo "WARNING: the previous release is not healthy either." >&2
    else
        echo "No previous release to roll back to; removing current." >&2
        rm -f "$CURRENT"
    fi
    # The failed release is no longer live; drop it so it doesn't take a pruning slot.
    if [[ $RELEASE != "$PREVIOUS" && $(readlink -f "$CURRENT" 2>/dev/null || true) != "$RELEASE" ]]; then
        rm -rf "$RELEASE"
    fi
    exit 1
fi

# --- Prune: keep the newest $KEEP_RELEASES, plus the live and the previous one --
log "Pruning old releases"
count=0
while IFS= read -r dir; do
    dir=${dir%/}
    count=$((count + 1))
    if ((count > KEEP_RELEASES)) && [[ $dir != "$RELEASE" && $dir != "$PREVIOUS" ]]; then
        echo "removing $(basename "$dir")"
        rm -rf "$dir"
    fi
done < <(ls -1dt "$RELEASES"/*/)
