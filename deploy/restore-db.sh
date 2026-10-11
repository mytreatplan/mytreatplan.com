#!/usr/bin/env bash
# Restore test for the off-region backups (story 1.14). Run on a team Mac, never on the
# server: it restores one encrypted dump made by `manage.py backupdb` into an EMPTY
# local PostgreSQL database whose name ends in `_restore_test`, then prints the number
# of applied migrations and of users to compare with staging.
#
#     createdb mytp_restore_test
#     bash deploy/restore-db.sh --identity <age-identity-file> --file <dump.age>
#     bash deploy/restore-db.sh --identity <age-identity-file> --latest
#     bash deploy/restore-db.sh --identity <age-identity-file> --object staging/mytreatplan-...dump.age
#
# Options:
#   --identity FILE  the age private key (from the team password manager); required.
#                    Process substitution works, e.g. --identity <(op read ...).
#   --file FILE      a dump already downloaded by hand, or
#   --object KEY     download this object from the bucket, or
#   --latest         download the newest object under BACKUP_PREFIX.
#   --db NAME        target database (default mytp_restore_test).
#   --dry-run        run every check (including that the target is empty), restore nothing.
#
# Downloads use the aws CLI with BACKUP_S3_ENDPOINT, BACKUP_S3_REGION, BACKUP_S3_BUCKET (and BACKUP_PREFIX
# for --latest) plus AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from the environment;
# never pass keys as arguments. Needs age, psql and pg_restore 17 or newer.
#
# The dump is decrypted in a pipe straight into pg_restore; no plaintext copy is written.
set -euo pipefail

MIN_PG_MAJOR=17
DB=mytp_restore_test
IDENTITY=
FILE=
OBJECT=
LATEST=0
DRY_RUN=0

die() { echo "ERROR: $*" >&2; exit 1; }
usage() {
    sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//' >&2
    exit 2
}

while (($#)); do
    case $1 in
        --identity) IDENTITY=${2:-}; shift 2 || usage ;;
        --file) FILE=${2:-}; shift 2 || usage ;;
        --object) OBJECT=${2:-}; shift 2 || usage ;;
        --latest) LATEST=1; shift ;;
        --db) DB=${2:-}; shift 2 || usage ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

# --- Guards (before anything touches a database) --------------------------------------
[[ $DB =~ ^[a-z0-9_]+_restore_test$ ]] \
    || die "refusing to restore into '$DB': the target database name must end in _restore_test"
[[ $DB != mytreatplan ]] || die "refusing to restore into mytreatplan"

sources=0
[[ -n $FILE ]] && sources=$((sources + 1))
[[ -n $OBJECT ]] && sources=$((sources + 1))
((LATEST)) && sources=$((sources + 1))
((sources == 1)) || die "give exactly one of --file, --object or --latest"
[[ -n $IDENTITY ]] || die "--identity <age-identity-file> is required"

# Local PostgreSQL only: never a remote host or a service definition that may point to one.
unset PGSERVICE PGDATABASE
case ${PGHOST:-} in
    ''|localhost|127.0.0.1|::1|/*) ;;
    *) die "refusing to restore to PGHOST=$PGHOST: restore tests use a local PostgreSQL" ;;
esac
case ${PGHOSTADDR:-} in
    ''|127.0.0.1|::1) ;;
    *) die "refusing to restore to PGHOSTADDR=$PGHOSTADDR: restore tests use a local PostgreSQL" ;;
esac

for tool in age psql pg_restore; do
    command -v "$tool" >/dev/null || die "$tool not found (brew install age postgresql@$MIN_PG_MAJOR)"
done
pg_major=$(pg_restore --version | sed -nE 's/.*\(PostgreSQL\) ([0-9]+).*/\1/p')
[[ -n $pg_major ]] || die "could not read the pg_restore version"
((pg_major >= MIN_PG_MAJOR)) \
    || die "pg_restore $pg_major is older than $MIN_PG_MAJOR (the server's version); install postgresql@$MIN_PG_MAJOR"
[[ -r $IDENTITY ]] || die "cannot read the identity file $IDENTITY"
if [[ -n $FILE ]]; then
    [[ -f $FILE && -r $FILE ]] || die "cannot read the dump $FILE"
fi
if [[ -z $FILE ]]; then
    command -v aws >/dev/null || die "aws CLI not found (brew install awscli), or download the dump by hand and use --file"
    [[ -n ${BACKUP_S3_ENDPOINT:-} && -n ${BACKUP_S3_REGION:-} && -n ${BACKUP_S3_BUCKET:-} ]] \
        || die "set BACKUP_S3_ENDPOINT, BACKUP_S3_REGION and BACKUP_S3_BUCKET (and AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)"
fi

psql_q() { psql -X -q -At -v ON_ERROR_STOP=1 -d "$DB" -c "$1"; }

relations=$(psql_q "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND n.nspname !~ '^pg_(toast|temp)' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')") \
    || die "cannot connect to local database '$DB'; create it first: createdb $DB"
[[ $relations =~ ^[0-9]+$ ]] || die "unexpected answer from psql: $relations"
((relations == 0)) \
    || die "refusing to restore: database '$DB' is not empty ($relations relations); dropdb $DB && createdb $DB"

if ((DRY_RUN)); then
    echo "Dry run: checks passed. Would restore ${FILE:-${OBJECT:-the newest object under ${BACKUP_PREFIX:-}}} into empty database '$DB'."
    exit 0
fi

# --- Download ---------------------------------------------------------------------------
export AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
s3() { aws --endpoint-url "$BACKUP_S3_ENDPOINT" --region "$BACKUP_S3_REGION" "$@"; }

if ((LATEST)); then
    OBJECT=$(s3 s3api list-objects-v2 --bucket "$BACKUP_S3_BUCKET" --prefix "${BACKUP_PREFIX:-}" \
        --query 'sort_by(Contents || `[]`, &LastModified)[-1].Key' --output text)
    [[ -n $OBJECT && $OBJECT != None ]] || die "no objects under '${BACKUP_PREFIX:-}' in $BACKUP_S3_BUCKET"
fi
if [[ -n $OBJECT ]]; then
    echo "==> Downloading $OBJECT"
    WORK=$(mktemp -d)
    trap 'rm -rf "$WORK"' EXIT
    FILE=$WORK/dump.age
    s3 s3 cp --only-show-errors "s3://$BACKUP_S3_BUCKET/$OBJECT" "$FILE"
fi

# --- Restore ----------------------------------------------------------------------------
echo "==> Restoring $(basename "${OBJECT:-$FILE}") into $DB"
age --decrypt --identity "$IDENTITY" "$FILE" \
    | pg_restore --no-owner --no-privileges --single-transaction --exit-on-error -d "$DB"

# --- Sanity check -----------------------------------------------------------------------
migrations=$(psql_q "SELECT count(*) FROM django_migrations")
users=$(psql_q "SELECT count(*) FROM accounts_user")
last=$(psql_q "SELECT app || '.' || name FROM django_migrations ORDER BY applied DESC, id DESC LIMIT 1")
echo "Restore OK into $DB"
echo "  applied migrations: $migrations (latest: $last)"
echo "  users:              $users"
echo "Compare them with staging (deploy/README.md, \"Backups and restore\")."
