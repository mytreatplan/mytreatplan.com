#!/usr/bin/env bash
# Rebuild the vendored mytp-core wheel from a tag (epic-platform entry 4, AD-10).
#
#   scripts/vendor-mytp-core.sh v0.1.0
#
# Clones the tag of mytreatplan/mytp-core (or MYTP_CORE_REPO: another URL or a local
# clone), checks the tag matches the package version, builds the wheel with the tag's
# commit time as SOURCE_DATE_EPOCH (the same tag gives the same file), replaces
# vendor/mytp_core-*.whl and points the mytp-core line of requirements.txt at it. Review
# and commit vendor/ and requirements.txt afterwards. Local dev, CI and the servers then
# install that file with `pip install -r requirements.txt` (from the repository root).
#
# Needs git and python3 (with pip; it fetches the pinned build backend from PyPI).
set -euo pipefail

die() { echo "vendor-mytp-core: $*" >&2; exit 1; }

[[ $# -eq 1 ]] || die "usage: scripts/vendor-mytp-core.sh <tag, e.g. v0.1.0>"
TAG=$1
[[ $TAG =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] || die "the tag must look like v<major>.<minor>.<patch>"
VERSION=${BASH_REMATCH[1]}
REPO=${MYTP_CORE_REPO:-git@github.com:mytreatplan/mytp-core.git}
PYTHON=${PYTHON:-python3}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WHEEL=mytp_core-$VERSION-py3-none-any.whl

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "vendor-mytp-core: cloning $TAG"
git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$TAG" "$REPO" "$TMP/src" 2>/dev/null \
    || git -c advice.detachedHead=false clone --quiet --branch "$TAG" "$REPO" "$TMP/src" \
    || die "cannot clone $TAG from $REPO"

declared=$("$PYTHON" -c "import sys, tomllib; print(tomllib.load(open(sys.argv[1], 'rb'))['project']['version'])" \
    "$TMP/src/pyproject.toml")
[[ $declared == "$VERSION" ]] || die "$TAG declares version $declared in pyproject.toml"

echo "vendor-mytp-core: building $WHEEL"
SOURCE_DATE_EPOCH=$(git -C "$TMP/src" log -1 --format=%ct)
export SOURCE_DATE_EPOCH
"$PYTHON" -m pip wheel --quiet --disable-pip-version-check --no-deps --no-cache-dir \
    --wheel-dir "$TMP/dist" "$TMP/src" </dev/null
[[ -f $TMP/dist/$WHEEL ]] || die "the build did not produce $WHEEL"

mkdir -p "$ROOT/vendor"
rm -f "$ROOT"/vendor/mytp_core-*.whl
cp "$TMP/dist/$WHEEL" "$ROOT/vendor/$WHEEL"

# requirements.txt: replace the mytp-core line (keeping its place), or add one.
REQ=$ROOT/requirements.txt
LINE=./vendor/$WHEEL
if grep -qE '^(\./)?vendor/mytp_core-.*\.whl$' "$REQ"; then
    awk -v line="$LINE" '/^(\.\/)?vendor\/mytp_core-.*\.whl$/ { print line; next } { print }' \
        "$REQ" >"$TMP/req" && cat "$TMP/req" >"$REQ"
else
    echo "$LINE" >>"$REQ"
fi

echo "vendor-mytp-core: vendor/$WHEEL ($(shasum -a 256 "$ROOT/vendor/$WHEEL" | cut -d' ' -f1))"
echo "vendor-mytp-core: requirements.txt -> $LINE; review and commit both"
