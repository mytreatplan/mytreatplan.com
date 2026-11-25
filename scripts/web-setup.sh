#!/usr/bin/env bash
# One-command setup for a Claude Code on the web session (or any fresh Linux box):
# Python venv with the dev requirements, Chromium for the browser tests, gettext and
# Pango, the Tailwind build, compiled translations, the BMad per-user config and a
# settings.env with CI's NON-SECRET test values. Safe to run again.
#
#   bash scripts/web-setup.sh
#   . .venv/bin/activate && python -m pytest -q -p no:cacheprovider --tb=line
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

say() { printf '\n== %s\n' "$*"; }

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
  done
fi
[ -n "$PY" ] || { echo "No python3 found" >&2; exit 1; }
"$PY" -c 'import sys; v=sys.version_info; print(f"Python {v.major}.{v.minor}"); sys.exit(0 if v >= (3, 13) else 1)' \
  || { echo "Python 3.13 or newer is required (CI uses 3.13)" >&2; exit 1; }

say "System packages (gettext, Pango)"
if command -v apt-get >/dev/null 2>&1; then
  SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq gettext libpango-1.0-0 libpangoft2-1.0-0 >/dev/null
else
  echo "apt-get not found: install gettext and Pango yourself"
fi

say "Python packages"
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements-dev.txt uv

say "Chromium for the browser tests"
python -m playwright install --with-deps chromium

say "Tailwind CSS"
npm ci --no-audit --no-fund
npm run build:css

say "Settings for tests (non-secret CI values)"
if [ ! -f settings.env ]; then
  cat > settings.env <<'EOF'
# Written by scripts/web-setup.sh: CI's non-secret test values. Never put real keys here.
SECRET_KEY=ci-only-not-a-secret
DEBUG=False
SECURE_SSL_REDIRECT=False
ALLOWED_HOSTS=localhost,127.0.0.1,testserver
DATABASE_URL=sqlite:////tmp/mytp-test.sqlite3
FIELD_ENCRYPTION_KEYS=test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=
BLIND_INDEX_KEY=dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=
EOF
  echo "settings.env written"
else
  echo "settings.env already exists: left as is"
fi

say "Translations"
python manage.py compilemessages -v 0

say "BMad per-user config"
mkdir -p _bmad/custom
if [ ! -f _bmad/custom/config.user.toml ]; then
  printf '[core]\nactive_initiative = "initiative-mytreatplan-portal-admin"\n' > _bmad/custom/config.user.toml
  echo "active initiative set"
fi

say "Check"
python manage.py check
echo
echo "Ready. Run the suite with:"
echo "  . .venv/bin/activate && python -m pytest -q -p no:cacheprovider --tb=line"
