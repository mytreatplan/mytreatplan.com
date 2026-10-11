#!/usr/bin/env bash
# Runs one scheduled job (deploy/jobs.conf) in the live release. Started as mytp by
# mytp-job@<name>.service, which its timer fires:
#
#     bash /srv/mytp/current/deploy/run-job.sh <name>
#
# Settings come from shared/settings.env, which every release links in as
# settings.env beside manage.py (Django reads it). `manage.py runjob` sends the Sentry
# cron check-ins; a failure exits non-zero, so the unit fails (see journalctl).
set -euo pipefail

APP_HOME=${APP_HOME:-/srv/mytp}

die() { echo "ERROR: $*" >&2; exit 1; }

[[ $# -eq 1 ]] || die "usage: $0 <job-name>"
NAME=$1
[[ $NAME =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "invalid job name: $NAME"
[[ -f $APP_HOME/shared/settings.env ]] || die "$APP_HOME/shared/settings.env is missing (see deploy/README.md)"
[[ -d $APP_HOME/current ]] || die "no live release at $APP_HOME/current"

# -P resolves `current` once, so a deploy switching it mid-run doesn't mix releases.
cd -P "$APP_HOME/current"
[[ -L settings.env || -f settings.env ]] || die "settings.env is not linked into $(pwd)"
exec ./venv/bin/python manage.py runjob "$NAME" </dev/null
