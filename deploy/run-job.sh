#!/usr/bin/env bash
# Runs one scheduled job (the site's deploy/jobs.conf) in the site's live release.
# Started as mytp-<site> by mytp-<site>-job@<name>.service, which its timer fires:
#
#     bash /srv/mytp-<site>/current/deploy/run-job.sh <site> <name>
#
# Settings come from shared/settings.env, which every release links in as
# settings.env beside manage.py (Django reads it). `manage.py runjob` sends the Sentry
# cron check-ins; a failure exits non-zero, so the unit fails (see journalctl).
set -euo pipefail

SITE_RE='^[a-z]([a-z0-9-]{0,18}[a-z0-9])?$'

die() { echo "ERROR: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 <site> <job-name>"
SITE=$1
NAME=$2
[[ $SITE =~ $SITE_RE ]] || die "invalid site name: '$SITE' (allowed: $SITE_RE)"
[[ $NAME =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "invalid job name: $NAME"
APP_HOME=/srv/mytp-$SITE
[[ -f $APP_HOME/shared/settings.env ]] || die "$APP_HOME/shared/settings.env is missing (see deploy/README.md)"
[[ -d $APP_HOME/current ]] || die "no live release at $APP_HOME/current"

# -P resolves `current` once, so a deploy switching it mid-run doesn't mix releases.
cd -P "$APP_HOME/current"
[[ -L settings.env || -f settings.env ]] || die "settings.env is not linked into $(pwd)"
exec ./venv/bin/python manage.py runjob "$NAME" </dev/null
