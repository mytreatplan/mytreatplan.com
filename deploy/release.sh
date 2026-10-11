#!/usr/bin/env bash
# Installs one release on the server and makes it live only if every step succeeds.
# Runs as the app user (mytp); the deploy-staging CI job copies it with the tarball:
#
#     bash release.sh <git-sha> <tarball>
#
# Flow: unpack to releases/<sha>, build its venv, compilemessages, collectstatic,
# migrate, createcachetable. Only then switch `current`, restart Gunicorn and check
# health through the socket. If the check fails, switch back, restart, exit non-zero.
# Keeps the last 5 releases.
set -euo pipefail

APP_HOME=${APP_HOME:-/srv/mytp}
KEEP_RELEASES=5
SOCKET=/run/mytp-gunicorn.sock
SERVICE=mytp-gunicorn
HEALTH_PATH=/robots.txt
HEALTH_TRIES=15

log() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 <git-sha> <tarball>"
SHA=$1
TARBALL=$2
[[ $SHA =~ ^[0-9a-f]{7,40}$ ]] || die "not a git sha: $SHA"
[[ -f $TARBALL ]] || die "tarball not found: $TARBALL"

RELEASES=$APP_HOME/releases
SHARED=$APP_HOME/shared
CURRENT=$APP_HOME/current
RELEASE=$RELEASES/$SHA

# The tarball and this script are one-off uploads.
cleanup() { rm -f "$TARBALL" "$APP_HOME/incoming/$SHA-release.sh"; }
trap cleanup EXIT

# One release at a time on this host (CI also serialises deploys).
exec 9>"$APP_HOME/.release.lock"
flock -w 600 9 || die "another release is still running"

[[ -f $SHARED/settings.env ]] || die "$SHARED/settings.env is missing (see deploy/README.md)"

# Health check: a request through the Gunicorn socket, as Nginx would send it.
site_host() {
    local hosts
    hosts=$(grep -E '^ALLOWED_HOSTS=' "$SHARED/settings.env" | tail -n1 | cut -d= -f2- | tr -d "\"' ")
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

log "Building venv"
python3 -m venv "$RELEASE/venv"
"$RELEASE/venv/bin/pip" install --quiet --disable-pip-version-check --no-cache-dir \
    -r "$RELEASE/requirements.txt" </dev/null

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
