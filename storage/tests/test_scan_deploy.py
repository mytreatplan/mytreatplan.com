"""Static checks of the scanner's deploy files (epic-platform entry 12): clamd's
configuration (limits, alerts, socket), provision.sh's ClamAV and scan worker steps, the
scan worker unit and its prune job. The scripts themselves run only on the server."""
import re
import shlex
import shutil
import subprocess

import pytest
from mytp_core import jobs

from storage import purposes, tasks

from .conftest import REPO_DEPLOY

CLAMD_CONF = REPO_DEPLOY / 'clamav' / 'clamd.conf'
PROVISION = REPO_DEPLOY / 'provision.sh'
SCAN_WORKER = REPO_DEPLOY / 'systemd' / 'scan-worker.service.template'


def clamd_options():
    options = {}
    for line in CLAMD_CONF.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            name, value = line.split(None, 1)
            assert name not in options, f'{name} is set twice'
            options[name] = value
    return options


def size(value):
    match = re.fullmatch(r'(\d+)([KM]?)', value)
    assert match, value
    return int(match.group(1)) * {'': 1, 'K': 1024, 'M': 1024 ** 2}[match.group(2)]


def unit_lines(text):
    joined = re.sub(r'\\\n\s*', ' ', text)
    return [line.strip() for line in joined.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def test_clamd_limits_cover_the_largest_upload():
    options = clamd_options()
    for name in ('StreamMaxLength', 'MaxFileSize', 'MaxScanSize'):
        assert size(options[name]) >= purposes.MAX_SCANNED_SIZE, name
    # A 1 GiB file is scanned before the scan task stops waiting.
    assert int(options['MaxScanTime']) >= 5 * 60 * 1000
    assert int(options['MaxScanTime']) < tasks.SCAN_TIMEOUT * 1000


def test_clamd_reports_what_it_cannot_scan_as_found():
    options = clamd_options()
    for name in ('AlertExceedsMax', 'AlertEncrypted', 'AlertEncryptedArchive',
                 'AlertEncryptedDoc', 'AlertBrokenExecutables', 'HeuristicAlerts'):
        assert options[name] == 'yes', name
    # Decision 2026-11-08: malformed images are scanned, not rejected for being malformed.
    assert options['AlertBrokenMedia'] == 'no'
    # Archive and recursion limits stay at clamd's defaults.
    for name in ('MaxRecursion', 'MaxFiles', 'MaxEmbeddedPE', 'MaxZipTypeRcg',
                 'MaxPartitions'):
        assert name not in options, name
    # No file type is switched off.
    assert not [name for name, value in options.items()
                if name.startswith('Scan') and value == 'no']


def test_clamd_socket_memory_and_spool():
    options = clamd_options()
    assert options['LocalSocket'] == '/run/clamav/clamd.ctl'
    assert (options['LocalSocketGroup'], options['LocalSocketMode']) == ('clamav', '660')
    assert options['ConcurrentDatabaseReload'] == 'no'
    assert options['TemporaryDirectory'] == '/var/lib/clamav/tmp'
    assert 'TCPSocket' not in options
    socket_dropin = (REPO_DEPLOY / 'clamav' / 'clamav-daemon.socket.conf').read_text()
    assert 'SocketMode=0660' in socket_dropin and 'SocketGroup=clamav' in socket_dropin
    service_dropin = unit_lines((REPO_DEPLOY / 'clamav' / 'clamav-daemon.service.conf').read_text())
    assert service_dropin == [
        '[Service]', 'ExecStart=',
        'ExecStart=/usr/sbin/clamd --foreground=true --config-file=/etc/clamav/clamd-mytp.conf']


def test_provision_installs_clamav_host_wide():
    script = PROVISION.read_text()
    packages = re.search(r'PACKAGES=\((.*?)\)', script, re.S).group(1).split()
    assert {'clamav-daemon', 'clamav-freshclam'} <= set(packages)
    for line in (
        'CLAMD_CONF=/etc/clamav/clamd-mytp.conf',
        'install_file "$DEPLOY_DIR/clamav/clamd.conf" "$CLAMD_CONF" 0644 root:root',
        'install_file "$DEPLOY_DIR/clamav/clamav-daemon.service.conf" "$CLAMD_SERVICE_DROPIN"',
        'install_file "$DEPLOY_DIR/clamav/clamav-daemon.socket.conf" "$CLAMD_SOCKET_DROPIN"',
        'ensure_dir "$CLAMD_TMP" 0700 clamav:clamav',
    ):
        assert line in script, line
    block = clamav_block(script)
    # Idempotent: each unit is enabled or started only when it is not, restarted only
    # when its files changed; a failure warns and the later steps still run.
    assert 'systemctl is-enabled --quiet "$unit"' in block
    assert 'if ! systemctl is-active --quiet clamav-daemon.service; then' in block
    assert 'elif ((clamd_changed)); then' in block
    assert 'exit' not in block and 'WARNING: clamav-daemon is not running' in block
    # The signatures are fetched only when one of the two databases is missing.
    assert 'if ! compgen -G "$CLAMAV_DB_DIR/main.c[vl]d"' in block
    assert '|| ! compgen -G "$CLAMAV_DB_DIR/daily.c[vl]d"' in block
    assert 'CLAMAV_DB_DIR=/var/lib/clamav\n' in script
    assert 'CLAMD_TMP=/var/lib/clamav/tmp\n' in script
    assert 'CLAMD_APPARMOR_LOCAL=/etc/apparmor.d/local/usr.sbin.clamd\n' in script


def test_apparmor_lets_clamd_read_its_config_and_use_its_spool():
    rules = [line.strip() for line in
             (REPO_DEPLOY / 'clamav' / 'apparmor-usr.sbin.clamd').read_text().splitlines()
             if line.strip() and not line.startswith('#')]
    options = clamd_options()
    assert rules == ['/etc/clamav/clamd-mytp.conf r,',
                     f"{options['TemporaryDirectory']}/ rw,",
                     f"{options['TemporaryDirectory']}/** rwk,"]


def clamav_block(script):
    return script[script.index('# --- Virus scanner'):script.index('# --- SSH')]


def run_clamav_step(tmp_path, *, enabled, active, signatures, apparmor=True,
                    clamd_starts=True):
    """provision.sh's "Virus scanner" step alone: sudo as a plain call; systemctl,
    freshclam, apparmor_parser and the install helpers as stubs logging to calls.log;
    every path in tmp_path. `active`: the units run already; `clamd_starts`: a start
    makes them run. Returns (result, calls)."""
    etc, db = tmp_path / 'etc', tmp_path / 'clamav-db'
    etc.mkdir(exist_ok=True)
    db.mkdir(exist_ok=True)
    (etc / 'usr.sbin.clamd').write_text('profile clamd {}\n')
    if signatures:
        (db / 'main.cvd').write_text('x')
        (db / 'daily.cld').write_text('x')
    calls = tmp_path / 'calls.log'
    q = shlex.quote
    prelude = f'''set -euo pipefail
sudo() {{ "$@"; }}
step() {{ echo "==> $*"; }}
CHANGES=0
changed() {{ CHANGES=$((CHANGES + 1)); echo "  changed: $*"; }}
install_file() {{ cmp -s "$1" "$2" 2>/dev/null && return 1; cp "$1" "$2"; changed "$2"; }}
ensure_dir() {{ [[ -d $1 ]] || {{ mkdir -p "$1"; changed "$1/"; }}; }}
CALLS={q(str(calls))}
: >"$CALLS"
systemctl() {{ echo "systemctl $*" >>"$CALLS"
    local unit=${{@: -1}}
    case $1 in
        is-enabled) return {0 if enabled else 1};;
        is-active)
            [[ $unit == apparmor.service ]] && return {0 if apparmor else 1}
            {'grep -qE "^systemctl (re)?start $unit$" "$CALLS" && return 0' if clamd_starts else ':'}
            return {0 if active else 1};;
    esac; }}
freshclam() {{ echo "freshclam $*" >>"$CALLS"; touch {q(str(db))}/main.cvd {q(str(db))}/daily.cld; }}
apparmor_parser() {{ echo "apparmor_parser $*" >>"$CALLS"; }}
DEPLOY_DIR={q(str(REPO_DEPLOY))}
CLAMD_CONF={q(str(etc / 'clamd-mytp.conf'))}
CLAMD_TMP={q(str(tmp_path / 'clamav-db' / 'tmp'))}
CLAMAV_DB_DIR={q(str(db))}
CLAMD_SERVICE_DROPIN={q(str(etc / 'clamav-daemon.service.d' / 'mytp.conf'))}
CLAMD_SOCKET_DROPIN={q(str(etc / 'clamav-daemon.socket.d' / 'mytp.conf'))}
CLAMD_APPARMOR_PROFILE={q(str(etc / 'usr.sbin.clamd'))}
CLAMD_APPARMOR_LOCAL={q(str(etc / 'apparmor.d' / 'local' / 'usr.sbin.clamd'))}
'''
    script = prelude + clamav_block(PROVISION.read_text()) + '\necho "CHANGES=$CHANGES"\n'
    result = subprocess.run(['bash', '-c', script], capture_output=True, text=True)
    return result, calls.read_text().splitlines()


bash_only = pytest.mark.skipif(shutil.which('bash') is None, reason='bash not available')


@bash_only
def test_the_clamav_step_installs_once_and_then_changes_nothing(tmp_path):
    result, calls = run_clamav_step(tmp_path, enabled=False, active=False, signatures=False)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'etc' / 'clamd-mytp.conf').read_text() == CLAMD_CONF.read_text()
    assert (tmp_path / 'etc' / 'apparmor.d' / 'local' / 'usr.sbin.clamd').exists()
    assert f"apparmor_parser -r {tmp_path / 'etc' / 'usr.sbin.clamd'}" in calls
    assert 'freshclam --quiet' in calls
    assert 'systemctl enable clamav-daemon.service' in calls
    assert 'systemctl start clamav-daemon.service' in calls
    assert 'systemctl daemon-reload' in calls
    assert 'changed: clamav-daemon started' in result.stdout
    assert 'WARNING' not in result.stderr

    # A second run on the provisioned host: no change, no restart, no reload.
    result, calls = run_clamav_step(tmp_path, enabled=True, active=True, signatures=True)
    assert result.returncode == 0, result.stderr
    assert 'CHANGES=0' in result.stdout, result.stdout
    assert not [c for c in calls if not c.startswith(('systemctl is-enabled',
                                                       'systemctl is-active'))], calls

    # A changed configuration restarts clamd (no AppArmor reload: its file is unchanged).
    (tmp_path / 'etc' / 'clamd-mytp.conf').write_text('old\n')
    result, calls = run_clamav_step(tmp_path, enabled=True, active=True, signatures=True)
    assert result.returncode == 0, result.stderr
    assert 'systemctl restart clamav-daemon.service' in calls
    assert 'freshclam --quiet' not in calls
    assert not [c for c in calls if c.startswith('apparmor_parser')]


@bash_only
def test_a_changed_apparmor_file_reloads_the_profile_only_when_apparmor_runs(tmp_path):
    run_clamav_step(tmp_path, enabled=True, active=True, signatures=True)
    local = tmp_path / 'etc' / 'apparmor.d' / 'local' / 'usr.sbin.clamd'
    local.write_text('old\n')
    result, calls = run_clamav_step(tmp_path, enabled=True, active=True, signatures=True,
                                    apparmor=False)
    assert result.returncode == 0, result.stderr
    assert not [c for c in calls if c.startswith('apparmor_parser')]
    assert local.read_text() == (REPO_DEPLOY / 'clamav' / 'apparmor-usr.sbin.clamd').read_text()


@bash_only
def test_signatures_are_fetched_when_one_database_is_missing(tmp_path):
    run_clamav_step(tmp_path, enabled=True, active=True, signatures=True)
    (tmp_path / 'clamav-db' / 'main.cvd').unlink()
    result, calls = run_clamav_step(tmp_path, enabled=True, active=True, signatures=False)
    assert result.returncode == 0, result.stderr
    assert 'freshclam --quiet' in calls


@bash_only
def test_a_clamd_that_does_not_come_up_is_a_warning_not_a_change(tmp_path):
    result, calls = run_clamav_step(tmp_path, enabled=True, active=False, signatures=True,
                                    clamd_starts=False)
    assert result.returncode == 0, result.stderr
    assert 'systemctl start clamav-daemon.service' in calls
    assert 'clamav-daemon started' not in result.stdout
    assert 'WARNING: clamav-daemon is not running after start' in result.stderr


def test_provision_installs_the_scan_worker_only_with_a_scanning_release():
    script = PROVISION.read_text()
    assert 'SCAN_WORKER=mytp-$SITE-scan-worker' in script
    block = script[script.index('step "Scan worker ($SITE)"'):script.index('# --- Scheduled jobs')]
    guard = block.index('sudo test -f "$APP_HOME/current/storage/clamd.py"')
    for line in (
        'sudo usermod -aG clamav "$APP_USER"',
        'render "$DEPLOY_DIR/systemd/scan-worker.service.template" "$WORK/scan-worker.service"',
        'install_file "$WORK/scan-worker.service" "/etc/systemd/system/$SCAN_WORKER.service" '
        '0644 root:root',
        'sudo systemctl enable "$SCAN_WORKER.service"',
    ):
        assert guard < block.index(line), line
    assert 'scan_worker_action=restart' in block
    assert 'exit' not in block and 'WARNING: $SCAN_WORKER did not' in block


def test_scan_worker_runs_only_the_scans_backend_and_follows_the_main_worker():
    lines = unit_lines(SCAN_WORKER.read_text().replace('__SITE__', 'dev'))
    assert 'User=mytp-dev' in lines and 'Group=mytp-dev' in lines
    assert 'SupplementaryGroups=clamav' in lines
    assert ('ExecStartPre=/srv/mytp-dev/current/venv/bin/python manage.py '
            'requeue_interrupted_tasks --backend scans') in lines
    exec_start = next(line for line in lines if line.startswith('ExecStart='))
    assert exec_start.startswith(
        'ExecStart=/srv/mytp-dev/current/venv/bin/python manage.py db_worker ')
    assert '--backend scans' in exec_start and '--queue-name scans' in exec_start
    assert '--no-reload' in exec_start
    # release.sh stops and restarts mytp-dev-worker; this unit goes with it.
    assert 'PartOf=mytp-dev-worker.service' in lines
    assert 'WantedBy=mytp-dev-worker.service' in lines
    assert 'Restart=always' in lines and 'NoNewPrivileges=true' in lines
    assert 'SyslogIdentifier=mytp-dev-scan-worker' in lines


def test_the_scans_backend_results_are_pruned_daily():
    declared = jobs.load_jobs(REPO_DEPLOY / 'jobs.conf')
    prune = declared['prunescans']
    assert prune.command == 'prune_db_task_results'
    assert prune.args == ('--backend', 'scans', '--queue-name', '*', '--min-age-days', '14',
                          '--failed-min-age-days', '30')
    assert declared['prunetasks'].schedule != prune.schedule
