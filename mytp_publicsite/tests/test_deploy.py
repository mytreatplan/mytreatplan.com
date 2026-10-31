"""Story 1.13: staging provisioning and deploy pipeline. Static checks of deploy/ and
the deploy-staging CI job (the scripts themselves run only on the server)."""
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings

from mytp_core import jobs as core_jobs

REPO = Path(settings.BASE_DIR)
DEPLOY = REPO / 'deploy'
WORKFLOW = REPO / '.github' / 'workflows' / 'ci.yml'
SCRIPTS = sorted(DEPLOY.glob('*.sh'))
bash_only = pytest.mark.skipif(shutil.which('bash') is None, reason='bash not available')


def test_deploy_scripts_exist():
    assert {p.name for p in SCRIPTS} >= {'provision.sh', 'release.sh'}


@pytest.mark.skipif(shutil.which('bash') is None, reason='bash not available')
@pytest.mark.parametrize('script', SCRIPTS, ids=lambda p: p.name)
def test_deploy_script_syntax(script):
    result = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope='module')
def workflow():
    yaml = pytest.importorskip('yaml')
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.fixture(scope='module')
def deploy_job(workflow):
    return workflow['jobs']['deploy-staging']


def test_deploy_job_needs_tests(deploy_job):
    needs = deploy_job['needs']
    assert needs == 'test' or needs == ['test']


def test_deploy_job_only_on_push_to_master(deploy_job):
    condition = re.sub(r'\s+', ' ', deploy_job['if'])
    assert "github.event_name == 'push'" in condition
    assert "github.ref == 'refs/heads/master'" in condition
    assert '||' not in condition


def test_deploy_job_environment_and_concurrency(deploy_job):
    environment = deploy_job['environment']
    name = environment['name'] if isinstance(environment, dict) else environment
    assert name == 'staging'
    assert deploy_job['concurrency']['group'] == 'deploy-staging'
    # Queue deploys; never cancel one halfway through a release.
    assert deploy_job['concurrency'].get('cancel-in-progress', False) is False


def test_deploy_job_uses_pinned_host_key(deploy_job):
    text = WORKFLOW.read_text()
    for secret in ('STAGING_HOST', 'STAGING_SSH_KEY', 'STAGING_KNOWN_HOSTS'):
        assert f'secrets.{secret}' in text
    assert 'StrictHostKeyChecking yes' in text
    assert 'ssh-keyscan' not in text
    assert 'StrictHostKeyChecking no' not in text
    assert 'accept-new' not in text


def test_deploy_job_ships_built_css_and_runs_release(deploy_job):
    commands = '\n'.join(step.get('run', '') for step in deploy_job['steps'])
    assert 'npm run build:css' in commands
    assert 'git archive' in commands
    assert 'static/css/site.css' in commands
    # The marker the release command requires, added after the build.
    assert ("tar --append -f release.tar static/css/site.css deploy/release-ready"
            in commands)
    assert commands.index('> deploy/release-ready') < commands.index('tar --append')


# --- Generic release check (epic-platform entry 10) -------------------------------------

def test_release_accepts_any_site_repos_build_not_the_portal_css():
    """release.sh checks what every site repo's CI build ships (manage.py and the
    deploy/release-ready marker), not a portal-only file, so the admin repo deploys too."""
    script = (DEPLOY / 'release.sh').read_text()
    assert 'site.css' not in script
    assert '[[ -f $RELEASE/manage.py ]] || die "tarball has no manage.py"' in script
    assert '[[ -f $RELEASE/deploy/release-ready ]] || die' in script
    # Checked right after unpacking, before anything is built from the release.
    unpack = script.index('tar -xzf "$TARBALL" -C "$RELEASE"')
    marker = script.index('$RELEASE/deploy/release-ready')
    assert unpack < marker < script.index('log "Building venv"')


def test_release_marker_is_never_committed():
    ignored = (REPO / '.gitignore').read_text().splitlines()
    assert '/deploy/release-ready' in ignored


def test_gunicorn_serves_the_wsgi_module_set_at_provision_time():
    """The unit's WSGI module is per site (provision.sh WSGI_MODULE, default the portal's
    mytreatplan.wsgi), so every release of dev, old ones included, keeps starting."""
    template = (DEPLOY / 'systemd' / 'gunicorn.service.template').read_text()
    assert 'gunicorn __WSGI_MODULE__:application ' in template
    script = (DEPLOY / 'provision.sh').read_text()
    assert 'WSGI_MODULE=${WSGI_MODULE:-mytreatplan.wsgi}' in script
    assert 'sed -i "s/__WSGI_MODULE__/$WSGI_MODULE/g" "$WORK/gunicorn.$kind"' in script
    assert settings.WSGI_APPLICATION == 'mytreatplan.wsgi.application'
    assert not (REPO / 'wsgi.py').exists()


@bash_only
@pytest.mark.parametrize('module', ['', 'my-admin.wsgi', 'x;rm', 'a..b', '.x'])
def test_provision_refuses_a_bad_wsgi_module_before_any_change(module, no_privileges):
    result = subprocess.run(
        ['bash', str(DEPLOY / 'provision.sh'), 'admindev', 'admindev.mytpdso.com', 'team'],
        capture_output=True, text=True,
        env={**no_privileges, 'WSGI_MODULE': module or ' '})
    assert result.returncode == 2
    assert 'Invalid WSGI_MODULE' in result.stderr
    assert 'STUB' not in result.stderr


def _release_command_step(tmp_path, installed_version):
    """Runs provision.sh's "Release command" step alone, with sudo as a plain call and the
    installed command in tmp_path. Returns (result, the installed file's text)."""
    script = (DEPLOY / 'provision.sh').read_text()
    block = script[script.index('# --- Release command'):script.index('# --- App user')]
    installed = tmp_path / 'mytp-release'
    if installed_version is not None:
        installed.write_text(f'#!/bin/bash\nRELEASE_TOOL_VERSION={installed_version}\n')
    prelude = (
        'set -euo pipefail\n'
        'sudo() { "$@"; }\n'
        'step() { echo "==> $*"; }\n'
        'install_file() { cp "$1" "$2"; echo "  changed: $2"; }\n'
        f'DEPLOY_DIR={shlex.quote(str(DEPLOY))}\n'
        f'RELEASE_COMMAND={shlex.quote(str(installed))}\n')
    result = subprocess.run(['bash', '-c', prelude + block], capture_output=True, text=True)
    return result, installed.read_text() if installed.exists() else None


def test_release_command_has_a_version():
    assert re.search(r'^RELEASE_TOOL_VERSION=2$', (DEPLOY / 'release.sh').read_text(), re.M)


@bash_only
@pytest.mark.parametrize('installed_version', [None, 1, 2])
def test_provision_installs_the_release_command_unless_it_would_downgrade(tmp_path,
                                                                         installed_version):
    result, installed = _release_command_step(tmp_path, installed_version)
    assert result.returncode == 0, result.stderr
    assert installed == (DEPLOY / 'release.sh').read_text()
    assert 'WARNING' not in result.stderr


@bash_only
def test_provision_skips_an_older_release_command_with_a_warning(tmp_path):
    result, installed = _release_command_step(tmp_path, 3)
    assert result.returncode == 0, result.stderr
    assert installed == '#!/bin/bash\nRELEASE_TOOL_VERSION=3\n'
    assert 'WARNING' in result.stderr and 'version 3' in result.stderr
    assert 'not replaced' in result.stderr


def test_deploy_job_deploys_the_dev_site_with_the_host_release_command(deploy_job):
    assert deploy_job['env']['SITE'] == 'dev'
    commands = '\n'.join(step.get('run', '') for step in deploy_job['steps'])
    assert 'User mytp-$SITE' in commands
    assert 'scp release.tar.gz "staging:/srv/mytp-$SITE/incoming/$SHA.tar.gz"' in commands
    assert ('ssh staging /usr/local/bin/mytp-release "$SITE" "$SHA" '
            '"/srv/mytp-$SITE/incoming/$SHA.tar.gz"') in commands
    # The release command is installed by provision.sh, not shipped with each deploy.
    assert 'scp deploy/release.sh' not in commands


def test_deploy_job_skips_superseded_commits_before_upload(deploy_job):
    steps = deploy_job['steps']
    names = [step.get('name', '') for step in steps]
    tip = next(i for i, step in enumerate(steps) if 'git ls-remote origin refs/heads/master' in step.get('run', ''))
    upload = names.index('Upload release')
    release = names.index('Release')
    assert tip < upload
    tip_id = steps[tip]['id']
    for i in (upload, release):
        assert steps[i]['if'] == f"steps.{tip_id}.outputs.latest == 'true'"


def test_sudoers_allows_only_the_app_and_worker_restarts():
    rules = [
        line for line in (DEPLOY / 'sudoers' / 'deploy.template').read_text().splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    assert rules == [
        'mytp-__SITE__ ALL=(root) NOPASSWD: /usr/bin/systemctl restart mytp-__SITE__-gunicorn',
        'mytp-__SITE__ ALL=(root) NOPASSWD: /usr/bin/systemctl stop mytp-__SITE__-worker',
        'mytp-__SITE__ ALL=(root) NOPASSWD: /usr/bin/systemctl restart mytp-__SITE__-worker',
    ]


def test_nginx_template_auth_redirect_and_proxy_headers():
    conf = (DEPLOY / 'nginx' / 'site.conf.template').read_text()
    assert 'server_name __DOMAIN__;' in conf
    assert 'return 301 https://__DOMAIN__$request_uri;' in conf
    assert '$host$request_uri' not in conf
    assert 'proxy_set_header X-Forwarded-Proto $scheme;' in conf
    assert 'auth_basic_user_file /etc/nginx/mytp-__SITE__.htpasswd;' in conf
    assert 'alias /srv/mytp-__SITE__/current/static_root/;' in conf
    assert 'upstream mytp-__SITE__-gunicorn {' in conf
    assert 'server unix:/run/mytp-__SITE__-gunicorn.sock fail_timeout=0;' in conf
    assert 'ssl_session_cache shared:mytp-__SITE__-ssl:10m;' in conf
    https = conf.split('# __HTTPS_BEGIN__', 1)[1]
    acme = re.search(r'location /\.well-known/acme-challenge/ \{(.*?)\}', https, re.S)
    assert acme and 'auth_basic off;' in acme.group(1)


def test_staging_env_example_has_no_secrets():
    values = dict(
        line.split('=', 1) for line in (DEPLOY / 'staging.env.example').read_text().splitlines()
        if line.strip() and not line.startswith('#')
    )
    assert values['SECRET_KEY'] == ''
    assert '<db-password>' in values['DATABASE_URL']
    assert values['DEBUG'] == 'False'
    assert values['ALLOWED_HOSTS'] == 'dev.mytpdso.com'
    assert values['ROBOTS_NOINDEX'] == 'True'
    assert values['DB_SSLMODE'] == 'verify-full'
    assert values['DB_SSLROOTCERT'] == '/srv/mytp-dev/shared/db-ca.pem'
    assert values['SITE'] == 'dev'
    assert values['REGION'] == 'eu'


# --- Site-aware deploy tooling (epic-platform entry 1) ---------------------------------

SITE_TEMPLATES = [
    DEPLOY / 'sudoers' / 'deploy.template',
    DEPLOY / 'systemd' / 'gunicorn.socket.template',
    DEPLOY / 'systemd' / 'gunicorn.service.template',
    DEPLOY / 'systemd' / 'job@.service.template',
    DEPLOY / 'systemd' / 'job.timer.template',
    DEPLOY / 'systemd' / 'worker.service.template',
    DEPLOY / 'nginx' / 'site.conf.template',
]
# Names of the single-site layout. `/srv/mytp` followed by anything but `-<site>`.
OLD_LAYOUT = re.compile(
    r'/srv/mytp(?![-\w])|mytp-gunicorn|mytp_gunicorn|mytp-job[@.]|/etc/nginx/mytp\.htpasswd'
    r'|sites-(?:available|enabled)/mytp\.conf|sudoers\.d/mytp-deploy|\bUser=mytp\b(?!-)'
    r'|mytp_ssl')
SITE_RE = '^[a-z]([a-z0-9-]{0,18}[a-z0-9])?$'


def test_deploy_folder_has_only_the_site_templates():
    found = {
        p for folder in ('systemd', 'nginx', 'sudoers') for p in (DEPLOY / folder).iterdir()
        if not p.name.startswith('.')
    }
    assert found == set(SITE_TEMPLATES)


@pytest.mark.parametrize('template', SITE_TEMPLATES, ids=lambda p: p.name)
def test_site_templates_use_the_site_placeholder(template):
    text = template.read_text()
    assert '__SITE__' in text
    assert not OLD_LAYOUT.search(text), OLD_LAYOUT.search(text).group(0)
    # Every unit, user, path or file it names is per site.
    for name in re.findall(r'mytp-[\w@.-]*', text):
        assert name.startswith(('mytp-__SITE__', 'mytp-<site>')), name


@pytest.mark.parametrize('path', [
    *SCRIPTS, WORKFLOW, DEPLOY / 'staging.env.example', DEPLOY / 'jobs.conf',
    REPO / 'mytreatplan' / 'settings.py', Path(core_jobs.__file__),
], ids=lambda p: p.name)
def test_no_single_site_layout_names_remain(path):
    match = OLD_LAYOUT.search(path.read_text())
    assert not match, match.group(0)


def _render(template, site):
    return template.read_text().replace('__SITE__', site)


def test_two_sites_share_no_names():
    """What provision.sh renders for dev and for dev-ae names disjoint users, paths, units
    and files (only host-wide names such as www-data may be shared)."""
    def names(site):
        found = set()
        for template in SITE_TEMPLATES:
            found |= set(re.findall(r'(?:/[\w.@-]+)+|mytp[-\w@.]*', _render(template, site)))
        return {n for n in found if 'mytp-' in n}

    dev, dev_ae = names('dev'), names('dev-ae')
    assert '/srv/mytp-dev/current/static_root' in dev
    assert '/run/mytp-dev-gunicorn.sock' in dev
    assert dev.isdisjoint(dev_ae), dev & dev_ae


def test_rendered_units_for_dev():
    service = _render(DEPLOY / 'systemd' / 'gunicorn.service.template', 'dev')
    assert 'Requires=mytp-dev-gunicorn.socket' in service
    assert 'User=mytp-dev' in service and 'Group=mytp-dev' in service
    assert 'WorkingDirectory=/srv/mytp-dev/current' in service
    socket = _render(DEPLOY / 'systemd' / 'gunicorn.socket.template', 'dev')
    assert 'ListenStream=/run/mytp-dev-gunicorn.sock' in socket
    assert 'SocketUser=mytp-dev' in socket
    job = _render(DEPLOY / 'systemd' / 'job@.service.template', 'dev')
    assert 'ExecStart=/bin/bash /srv/mytp-dev/current/deploy/run-job.sh dev %i' in job
    sudoers = _render(DEPLOY / 'sudoers' / 'deploy.template', 'dev')
    assert 'mytp-dev ALL=(root) NOPASSWD: /usr/bin/systemctl restart mytp-dev-gunicorn' in sudoers
    assert 'mytp-dev ALL=(root) NOPASSWD: /usr/bin/systemctl restart mytp-dev-worker' in sudoers
    worker = _render(DEPLOY / 'systemd' / 'worker.service.template', 'dev')
    assert 'User=mytp-dev' in worker and 'Group=mytp-dev' in worker
    assert 'WorkingDirectory=/srv/mytp-dev/current' in worker


def test_provision_names_every_per_site_artifact_from_the_site():
    script = (DEPLOY / 'provision.sh').read_text()
    for line in (
        'APP_USER=mytp-$SITE',
        'APP_HOME=/srv/mytp-$SITE',
        'HTPASSWD_FILE=/etc/nginx/mytp-$SITE.htpasswd',
        'NGINX_SITE=/etc/nginx/sites-available/mytp-$SITE.conf',
        'NGINX_ENABLED=/etc/nginx/sites-enabled/mytp-$SITE.conf',
        'SUDOERS_FILE=/etc/sudoers.d/mytp-$SITE-deploy',
        'GUNICORN=mytp-$SITE-gunicorn',
        'JOB=mytp-$SITE-job',
        'WORKER=mytp-$SITE-worker',
        'RELEASE_COMMAND=/usr/local/bin/mytp-release',
        'install_file "$DEPLOY_DIR/release.sh" "$RELEASE_COMMAND" 0755 root:root',
    ):
        assert line in script, line
    assert f"SITE_RE='{SITE_RE}'" in script


def test_scripts_and_settings_share_the_site_pattern():
    for name in ('provision.sh', 'release.sh', 'run-job.sh'):
        assert f"SITE_RE='{SITE_RE}'" in (DEPLOY / name).read_text(), name
    settings_text = (REPO / 'mytreatplan' / 'settings.py').read_text()
    assert f"re.fullmatch(r'{SITE_RE[1:-1]}', SITE)" in settings_text


def test_site_home_derives_from_site():
    from mytp_core import jobs

    assert settings.SITE_HOME == Path('/srv') / f'mytp-{settings.SITE}'
    assert re.fullmatch(SITE_RE[1:-1], settings.SITE)
    assert jobs.installed_jobs_file() == settings.SITE_HOME / 'shared' / 'jobs.conf'
    assert jobs.release_jobs_file().is_file()


def test_bad_site_setting_is_refused():
    result = subprocess.run(
        [sys.executable, '-c', 'import django; django.setup()'],
        cwd=REPO, capture_output=True, text=True,
        env={**os.environ, 'SITE': 'Dev/../x', 'SECRET_KEY': 'test-only-not-secret',
             'DJANGO_SETTINGS_MODULE': 'mytreatplan.settings'})
    assert result.returncode != 0
    assert 'ImproperlyConfigured' in result.stderr and 'SITE must be' in result.stderr


@pytest.fixture
def no_privileges(tmp_path):
    """PATH with a `sudo` (and the other privileged tools) that fail loudly, so a script
    under test can never change this machine."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for tool in ('sudo', 'useradd', 'systemctl', 'apt-get', 'dpkg-query', 'ufw', 'nginx'):
        stub = bin_dir / tool
        stub.write_text('#!/bin/sh\necho "STUB $0 called" >&2\nexit 97\n')
        stub.chmod(0o755)
    return {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}'}




@bash_only
@pytest.mark.parametrize('site', ['Dev', 'dev site', '../dev', 'dev/x', '-dev', 'dev-', '9dev',
                                  'dev_ae', 'a' * 21, ''])
def test_provision_refuses_bad_site_names_before_any_change(site, no_privileges):
    result = subprocess.run(
        ['bash', str(DEPLOY / 'provision.sh'), site, 'dev.mytpdso.com', 'team'],
        capture_output=True, text=True, env=no_privileges)
    assert result.returncode == 2
    assert 'Invalid site name' in result.stderr and SITE_RE in result.stderr
    assert 'STUB' not in result.stderr


@bash_only
def test_provision_needs_the_site_first(no_privileges):
    result = subprocess.run(['bash', str(DEPLOY / 'provision.sh'), 'dev.mytpdso.com', 'team'],
                            capture_output=True, text=True, env=no_privileges)
    assert result.returncode == 2
    assert 'Usage:' in result.stderr and '<site> <domain> <htpasswd-user>' in result.stderr


@bash_only
def test_release_refuses_an_unprovisioned_site(tmp_path, no_privileges):
    site = 'nosuchsite-test'
    assert not Path(f'/srv/mytp-{site}').exists()
    result = subprocess.run(
        ['bash', str(DEPLOY / 'release.sh'), site, '0123abc', str(tmp_path / 'x.tar.gz')],
        capture_output=True, text=True, env=no_privileges)
    assert result.returncode == 1
    assert f'/srv/mytp-{site} is missing' in result.stderr


@bash_only
@pytest.mark.parametrize('script,args', [
    ('release.sh', ['Bad', '0123abc', '/nonexistent.tar.gz']),
    ('run-job.sh', ['Bad', 'clearsessions']),
])
def test_scripts_refuse_bad_site_names(script, args, no_privileges):
    result = subprocess.run(['bash', str(DEPLOY / script), *args],
                            capture_output=True, text=True, env=no_privileges)
    assert result.returncode == 1
    assert 'invalid site name' in result.stderr


def test_release_checks_the_site_in_settings_env():
    script = (DEPLOY / 'release.sh').read_text()
    assert '[[ $# -eq 3 ]] || die "usage: $0 <site> <git-sha> <tarball>"' in script
    assert 'APP_HOME=/srv/mytp-$SITE' in script
    assert 'SOCKET=/run/mytp-$SITE-gunicorn.sock' in script
    assert 'SERVICE=mytp-$SITE-gunicorn' in script
    assert '[[ $(id -un) == "$APP_USER" ]]' in script
    assert '[[ $env_site == "$SITE" ]]' in script
    # The previous flow is kept.
    for step in ('flock -w 600 9', 'manage migrate --noinput', 'point_current_at "$PREVIOUS"',
                 'KEEP_RELEASES=5'):
        assert step in script, step


# --- Background task worker (AD-8, epic-platform entry 9) --------------------------------

def _unit_lines(text):
    """The unit's directives, with backslash continuations joined."""
    joined = re.sub(r'\\\n\s*', ' ', text)
    return [line.strip() for line in joined.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def test_worker_unit_runs_db_worker_in_the_live_release_as_the_site_user():
    lines = _unit_lines(_render(DEPLOY / 'systemd' / 'worker.service.template', 'dev'))
    assert 'Type=exec' in lines
    assert 'User=mytp-dev' in lines and 'Group=mytp-dev' in lines
    assert 'WorkingDirectory=/srv/mytp-dev/current' in lines
    # Interrupted tasks go back to the queue before the worker starts.
    assert ('ExecStartPre=/srv/mytp-dev/current/venv/bin/python manage.py '
            'requeue_interrupted_tasks') in lines
    exec_start = next(line for line in lines if line.startswith('ExecStart='))
    assert exec_start.startswith(
        'ExecStart=/srv/mytp-dev/current/venv/bin/python manage.py db_worker ')
    # Every queue, not only `default`.
    assert '--no-reload' in exec_start and "--queue-name '*'" in exec_start
    # systemd brings a killed or crashed worker back, but gives up on one that keeps failing.
    assert 'Restart=always' in lines
    assert 'StartLimitIntervalSec=300' in lines and 'StartLimitBurst=10' in lines
    assert 'KillSignal=SIGTERM' in lines
    assert 'WantedBy=multi-user.target' in lines
    assert 'NoNewPrivileges=true' in lines


def test_provision_installs_enables_and_starts_the_worker():
    script = (DEPLOY / 'provision.sh').read_text()
    for line in (
        'render "$DEPLOY_DIR/systemd/worker.service.template" "$WORK/worker.service"',
        'install_file "$WORK/worker.service" "/etc/systemd/system/$WORKER.service" 0644 root:root',
        'sudo systemctl enable "$WORKER.service"',
        'sudo systemctl "$worker_action" "$WORKER.service"',
    ):
        assert line in script, line
    # Installed, enabled and started only when the live release ships the worker.
    block = script[script.index('step "Task worker ($SITE)"'):script.index('# --- Scheduled jobs')]
    guard = block.index("sudo grep -qi '^django-tasks-db==' \"$APP_HOME/current/requirements.txt\"")
    assert guard < block.index('install_file "$WORK/worker.service"')
    assert guard < block.index('sudo systemctl enable "$WORKER.service"')
    assert 'worker_action=restart' in block
    # A worker that cannot start warns; the later steps (jobs, Nginx, TLS) still run.
    assert 'exit 1' not in block and 'WARNING: $WORKER did not $worker_action' in block
    assert re.search(r'^django-tasks-db==', (REPO / 'requirements.txt').read_text(), re.M | re.I)


def test_release_restarts_the_worker_after_the_switch_and_on_rollback():
    script = (DEPLOY / 'release.sh').read_text()
    assert 'WORKER=mytp-$SITE-worker' in script
    # The exact commands the sudoers rules allow.
    assert 'sudo -n /usr/bin/systemctl stop "$WORKER"' in script
    assert 'sudo -n /usr/bin/systemctl restart "$WORKER"' in script
    assert 'systemctl is-active --quiet "$WORKER"' in script
    # A site whose unit is not enabled (not provisioned with it) deploys as before.
    assert 'has_worker() { systemctl is-enabled --quiet "$WORKER" 2>/dev/null; }' in script
    # Stopped before migrate, started after the switch: never old code on the new schema.
    stop = script.index('\nstop_worker\n')
    migrate = script.index('\nmanage migrate --noinput')
    switch = script.index('point_current_at "$RELEASE"')
    live = script.index('if ((restart_status == 0)) && healthy && restart_worker; then')
    rollback = script.index('point_current_at "$PREVIOUS"', live)
    assert stop < migrate < switch < live < rollback
    # Every rollback restarts both services on the previous release.
    after = script[rollback:script.index('exit 1', rollback)]
    assert 'restart_app' in after and 'restart_worker' in after
    # A failure before the switch starts the stopped worker again on the unchanged release.
    on_error = script[script.index('on_build_error() {'):script.index("trap 'on_build_error")]
    assert 'if ((worker_stopped)); then' in on_error and 'restart_worker' in on_error


def test_two_sites_have_their_own_workers():
    dev = _render(DEPLOY / 'systemd' / 'worker.service.template', 'dev')
    dev_ae = _render(DEPLOY / 'systemd' / 'worker.service.template', 'dev-ae')
    assert '/srv/mytp-dev/' in dev and '/srv/mytp-dev/' not in dev_ae
    assert 'SyslogIdentifier=mytp-dev-ae-worker' in dev_ae
