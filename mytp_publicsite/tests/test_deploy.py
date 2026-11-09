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
    assert environment['url'] == 'https://${{ matrix.domain }}'
    # One queue per site: a dev-ae deploy never waits on (or holds up) dev's.
    assert deploy_job['concurrency']['group'] == 'deploy-staging-${{ matrix.site }}'
    # Queue deploys; never cancel one halfway through a release.
    assert deploy_job['concurrency'].get('cancel-in-progress', False) is False


def test_deploy_job_uses_pinned_host_key(deploy_job):
    text = WORKFLOW.read_text()
    for secret in ('STAGING_HOST', 'STAGING_KNOWN_HOSTS'):
        assert f'secrets.{secret}' in text
    # Each site's deploy key comes from its own secret, picked by the matrix leg.
    assert 'STAGING_SSH_KEY: ${{ secrets[matrix.ssh_key_secret] }}' in text
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


# --- dev-ae beside dev (epic-platform entry 7) ---------------------------------------------

def test_deploy_job_deploys_dev_and_dev_ae_with_their_own_keys(deploy_job):
    strategy = deploy_job['strategy']
    assert strategy['matrix'] == {'include': [
        {'site': 'dev', 'domain': 'dev.mytpdso.com', 'ssh_key_secret': 'STAGING_SSH_KEY'},
        {'site': 'dev-ae', 'domain': 'dev-ae.mytpdso.com',
         'ssh_key_secret': 'STAGING_SSH_KEY_DEV_AE'},
    ]}
    assert deploy_job['env']['SITE'] == '${{ matrix.site }}'
    # Both legs deploy the commit that was pushed.
    assert deploy_job['env']['SHA'] == '${{ github.sha }}'


def test_a_failing_dev_ae_deploy_cannot_stop_dev(deploy_job, workflow):
    # fail-fast would cancel the other leg when one fails.
    assert deploy_job['strategy']['fail-fast'] is False
    assert 'continue-on-error' not in deploy_job
    # Nothing waits on the deploy job, and no step stops on another site's result.
    for name, job in workflow['jobs'].items():
        needs = job.get('needs', [])
        assert 'deploy-staging' not in ([needs] if isinstance(needs, str) else needs), name
    text = '\n'.join(f"{step.get('if', '')}\n{step.get('run', '')}" for step in deploy_job['steps'])
    assert 'dev-ae' not in text and 'matrix.site ==' not in text


def test_deploy_job_refuses_a_missing_site_key(deploy_job):
    configure = next(step for step in deploy_job['steps'] if step.get('name') == 'Configure SSH')
    run = configure['run']
    assert run.index('if [ -z "$STAGING_SSH_KEY" ]; then') < run.index('> ~/.ssh/deploy_key')


def test_deploy_job_deploys_each_site_with_the_host_release_command(deploy_job):
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
    DEPLOY / 'systemd' / 'scan-worker.service.template',
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
    for tool in ('sudo', 'useradd', 'systemctl', 'apt-get', 'dpkg-query', 'ufw', 'nginx', 'curl', 'gpg'):
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


# --- Redirect-only names (epic-regions entry 7: mytreatplan.es / .ie) -------------------

EXTRA = 'mytreatplan.es www.mytreatplan.es mytreatplan.ie www.mytreatplan.ie'
EXTRA_NAMES = EXTRA.split()
REDIRECT = 'return 301 https://mytreatplan.com$request_uri;'


def _provision_slice(start, end):
    script = (DEPLOY / 'provision.sh').read_text()
    return script[script.index(start):script.index(end)]


def _run_provision_steps(tmp_path, *blocks, extra='', sans=None, cert=False, ip_of=None,
                         installed=None):
    """Runs the EXTRA_DOMAINS validation and then the given provision.sh blocks for the
    site prod on mytreatplan.com, with sudo as a plain call and nginx, systemctl, openssl,
    certbot, getent and ip as stubs that log to calls.log. Every path is in tmp_path.
    `sans`: the names the existing certificate covers; `ip_of`: name -> IPv4 (default
    every name resolves to this host). Returns (result, site.conf text or None, calls)."""
    work, live = tmp_path / 'work', tmp_path / 'live'
    work.mkdir()
    live.mkdir()
    site = tmp_path / 'mytp-prod.conf'
    if installed is not None:
        site.write_text(installed)
    cert_file, sans_file = live / 'fullchain.pem', tmp_path / 'sans'
    if cert:
        cert_file.write_text('cert')
        sans_file.write_text(', '.join(f'DNS:{n}' for n in (sans or ['mytreatplan.com'])))
    hosts = '\n'.join(f'{n} {ip}' for n, ip in (ip_of or {}).items())
    q = shlex.quote
    prelude = f'''set -euo pipefail
sudo() {{ "$@"; }}
step() {{ echo "==> $*"; }}
changed() {{ echo "  changed: $*"; }}
install_file() {{ cmp -s "$1" "$2" 2>/dev/null && return 1; cp "$1" "$2"; changed "$2"; }}
CALLS={q(str(tmp_path / 'calls.log'))}
: >"$CALLS"
nginx() {{ echo "nginx $*" >>"$CALLS"; }}
systemctl() {{ echo "systemctl $*" >>"$CALLS"; }}
openssl() {{ echo "openssl $*" >>"$CALLS"
    printf 'X509v3 Subject Alternative Name: \\n    %s\\n' "$(cat {q(str(sans_file))})"; }}
certbot() {{ {{ printf certbot; printf ' %q' "$@"; echo; }} >>"$CALLS"; echo cert >"$CERT_FILE"
    local out="" prev=""
    for a in "$@"; do [[ $prev == -d ]] && out="${{out:+$out, }}DNS:$a"; prev=$a; done
    printf '%s' "$out" >{q(str(sans_file))}; }}
getent() {{ local ip
    ip=$(awk -v n="$2" '$1 == n {{print $2}}' <<<{q(hosts)})
    [[ $ip == none ]] && return 2
    echo "${{ip:-192.0.2.10}} STREAM $2"; }}
ip() {{ echo "2: eth0    inet 192.0.2.10/24 brd 192.0.2.255 scope global eth0"; }}
SITE=prod
DOMAIN=mytreatplan.com
EXTRA_DOMAINS={q(extra)}
DEPLOY_DIR={q(str(DEPLOY))}
WORK={q(str(work))}
NGINX_SITE={q(str(site))}
CERT_FILE={q(str(cert_file))}
CHANGES=0
'''
    validation = _provision_slice('# EXTRA_DOMAINS: each name', 'if [[ $EUID -eq 0 ]]')
    script = prelude + validation + ''.join(blocks)
    result = subprocess.run(['bash', '-c', script], capture_output=True, text=True)
    calls = (tmp_path / 'calls.log').read_text().splitlines()
    return result, site.read_text() if site.exists() else None, calls


NGINX_FUNCTIONS = ('# --- Nginx site', 'step "Nginx ($SITE)"')


def _render_site(tmp_path, full, **kwargs):
    block = _provision_slice(*NGINX_FUNCTIONS) + f'render_nginx {full}\n'
    result, conf, calls = _run_provision_steps(tmp_path, block, **kwargs)
    assert result.returncode == 0, result.stderr
    return conf, calls


def _server_blocks(conf):
    return re.findall(r'^server \{\n.*?^\}', conf, re.S | re.M)


def _redirect_block(conf):
    blocks = [b for b in _server_blocks(conf) if 'server_name mytreatplan.es' in b]
    return blocks[0] if blocks else None


@bash_only
@pytest.mark.parametrize('full', [0, 1])
def test_without_extra_domains_the_site_renders_as_before(tmp_path, full):
    """Unset: the old render, comments aside (only the domain in server_name, no extra
    block, no openssl call)."""
    conf, calls = _render_site(tmp_path, full, cert=bool(full))
    old = (DEPLOY / 'nginx' / 'site.conf.template').read_text()
    old = re.sub(r'\n# __EXTRA_BEGIN__\n.*?# __EXTRA_END__\n', '\n', old, flags=re.S)
    old = old.replace(' __EXTRA_DOMAINS__', '').replace('__DOMAIN__', 'mytreatplan.com')
    old = old.replace('__SITE__', 'prod')
    if not full:
        old = old[:old.index('\n# __HTTPS_BEGIN__\n') + 1]
    strip = lambda text: [line for line in text.splitlines() if not line.startswith('#')]  # noqa: E731
    assert strip(conf) == strip(old)
    assert '__' not in '\n'.join(strip(conf))
    assert 'mytreatplan.es' not in conf and '# __EXTRA_BEGIN__' not in conf
    assert re.findall(r'server_name ([^;]*);', conf) == ['mytreatplan.com'] * (1 + full)
    assert len(_server_blocks(conf)) == 1 + full
    assert not [c for c in calls if c.startswith('openssl')]


@bash_only
def test_the_http_only_render_keeps_the_port_80_server(tmp_path):
    """The HTTP-only cut starts at the marker line itself, not at the header comment
    that mentions it, so certbot's challenge is answered before the first certificate."""
    conf, _ = _render_site(tmp_path, 0, extra=EXTRA)
    port_80 = _server_blocks(conf)
    assert len(port_80) == 1 and 'listen 80;' in port_80[0]
    assert f'server_name mytreatplan.com {EXTRA};' in port_80[0]
    assert 'location /.well-known/acme-challenge/ {' in port_80[0]
    assert REDIRECT in port_80[0]
    assert 'listen 443' not in conf and 'upstream mytp-prod-gunicorn' in conf


@bash_only
def test_extra_domains_redirect_in_one_hop_without_a_password(tmp_path):
    conf, _ = _render_site(tmp_path, 1, extra=EXTRA, cert=True, sans=['mytreatplan.com', *EXTRA_NAMES])
    blocks = _server_blocks(conf)
    assert len(blocks) == 3
    assert f'server_name mytreatplan.com {EXTRA};' in blocks[0] and REDIRECT in blocks[0]
    assert 'server_name mytreatplan.com;' in blocks[1] and 'auth_basic "' in blocks[1]
    redirect = _redirect_block(conf)
    assert redirect == blocks[2]
    assert f'server_name {EXTRA};' in redirect
    assert 'listen 443 ssl;' in redirect and 'listen [::]:443 ssl;' in redirect
    assert 'ssl_certificate /etc/letsencrypt/live/mytreatplan.com/fullchain.pem;' in redirect
    assert 'ssl_certificate_key /etc/letsencrypt/live/mytreatplan.com/privkey.pem;' in redirect
    assert REDIRECT in redirect
    assert 'auth_basic' not in redirect and 'proxy_pass' not in redirect
    assert '__' not in '\n'.join(line for line in conf.splitlines() if not line.startswith('#'))


@bash_only
def test_the_redirect_block_waits_for_a_certificate_covering_the_extra_names(tmp_path):
    conf, _ = _render_site(tmp_path, 1, extra=EXTRA, cert=True, sans=['mytreatplan.com', 'mytreatplan.es'])
    assert _redirect_block(conf) is None
    assert f'server_name mytreatplan.com {EXTRA};' in conf
    assert len(_server_blocks(conf)) == 2


@bash_only
def test_extra_domains_are_normalised(tmp_path):
    conf, _ = _render_site(tmp_path, 0, extra='  MyTreatplan.es\twww.mytreatplan.es  ')
    assert 'server_name mytreatplan.com mytreatplan.es www.mytreatplan.es;' in conf


@bash_only
@pytest.mark.parametrize('extra', [
    'mytreatplan.es;rm', 'mytreatplan.es $(id)', 'mytreatplan.es/x', 'mytreatplan.com',
    'mytreatplan.es MyTreatplan.com', 'mytreatplan.es www.mytreatplan.es mytreatplan.es',
    'mytreatplan.es MYTREATPLAN.ES', 'mytreatplan.*',
])
def test_provision_refuses_bad_extra_domains_before_any_change(extra, no_privileges):
    result = subprocess.run(
        ['bash', str(DEPLOY / 'provision.sh'), 'prod', 'mytreatplan.com', 'team'],
        capture_output=True, text=True, env={**no_privileges, 'EXTRA_DOMAINS': extra})
    assert result.returncode == 2
    assert 'Invalid EXTRA_DOMAINS' in result.stderr
    assert 'STUB' not in result.stderr


@bash_only
def test_provision_warns_when_a_run_without_extra_domains_drops_the_redirect(tmp_path):
    with_redirect = tmp_path / 'with'
    with_redirect.mkdir()
    conf, _ = _render_site(with_redirect, 1, extra=EXTRA, cert=True, sans=['mytreatplan.com', *EXTRA_NAMES])
    nginx_step = _provision_slice('step "Nginx ($SITE)"', 'if [[ -L /etc/nginx/sites-enabled/default ]]')
    for extra, warned in (('', True), (EXTRA, False)):
        run = tmp_path / ('unset' if not extra else 'set')
        run.mkdir()
        result, _, _ = _run_provision_steps(run, nginx_step, extra=extra, installed=conf)
        assert result.returncode == 0, result.stderr
        assert ('WARNING' in result.stderr) is warned
        if warned:
            assert 'EXTRA_DOMAINS is not set' in result.stderr
            assert 'removes that redirect' in result.stderr
    # Extras only on port 80 (the certificate does not cover them yet): still a redirect.
    port_80_only = tmp_path / 'port-80-only'
    port_80_only.mkdir()
    conf, _ = _render_site(port_80_only, 1, extra=EXTRA, cert=True, sans=['mytreatplan.com'])
    assert '# __EXTRA_BEGIN__' not in conf and f'server_name mytreatplan.com {EXTRA};' in conf
    run = tmp_path / 'port-80-only-unset'
    run.mkdir()
    result, _, _ = _run_provision_steps(run, nginx_step, installed=conf)
    assert result.returncode == 0, result.stderr
    assert 'EXTRA_DOMAINS is not set' in result.stderr
    # A site that never had extras: no warning.
    plain_render = tmp_path / 'plain-render'
    plain_render.mkdir()
    plain_conf, _ = _render_site(plain_render, 1, cert=True)
    plain = tmp_path / 'plain'
    plain.mkdir()
    result, _, _ = _run_provision_steps(plain, nginx_step, installed=plain_conf)
    assert result.returncode == 0, result.stderr
    assert 'WARNING' not in result.stderr


TLS_STEP = ('# --- TLS certificate', '\necho\nif ((CHANGES)); then')


def _tls(tmp_path, **kwargs):
    blocks = (_provision_slice(*NGINX_FUNCTIONS), _provision_slice(*TLS_STEP))
    result, conf, calls = _run_provision_steps(tmp_path, *blocks, **kwargs)
    assert result.returncode == 0, result.stderr
    certbot = [shlex.split(c)[1:] for c in calls if c.startswith('certbot ')]
    return result, conf, certbot


def _domains(args):
    return [args[i + 1] for i, a in enumerate(args) if a == '-d']


@bash_only
def test_the_first_certificate_covers_the_domain_and_the_extra_names(tmp_path):
    result, conf, certbot = _tls(tmp_path, extra=EXTRA)
    assert len(certbot) == 1
    args = certbot[0]
    assert args[0] == 'certonly' and '--expand' not in args
    assert _domains(args) == ['mytreatplan.com', *EXTRA_NAMES]
    assert args[-2:] == ['--deploy-hook', 'systemctl reload nginx']
    # Re-rendered with the new certificate: the redirect block is in.
    assert _redirect_block(conf) and REDIRECT in _redirect_block(conf)


@bash_only
def test_without_extra_domains_the_certificate_step_is_unchanged(tmp_path):
    _, _, certbot = _tls(tmp_path)
    assert len(certbot) == 1 and _domains(certbot[0]) == ['mytreatplan.com']
    assert '--expand' not in certbot[0]
    again = tmp_path / 'again'
    again.mkdir()
    _, _, certbot = _tls(again, cert=True)
    assert certbot == []


@bash_only
def test_a_certificate_lacking_an_extra_name_is_expanded(tmp_path):
    result, conf, certbot = _tls(tmp_path, extra=EXTRA, cert=True,
                                 sans=['mytreatplan.com', 'mytreatplan.es', 'www.mytreatplan.es'])
    assert len(certbot) == 1
    assert certbot[0][:2] == ['certonly', '--expand']
    assert _domains(certbot[0]) == ['mytreatplan.com', *EXTRA_NAMES]
    assert _redirect_block(conf) is not None


@bash_only
def test_a_certificate_covering_every_name_is_left_alone(tmp_path):
    result, conf, certbot = _tls(tmp_path, extra=EXTRA, cert=True,
                                 sans=['mytreatplan.com', *EXTRA_NAMES])
    assert certbot == [] and 'WARNING' not in result.stderr


@bash_only
@pytest.mark.parametrize('cert', [False, True])
def test_an_extra_name_not_pointing_here_skips_the_certificate(tmp_path, cert):
    result, conf, certbot = _tls(tmp_path, extra=EXTRA, cert=cert,
                                 ip_of={'www.mytreatplan.ie': '203.0.113.7',
                                        'mytreatplan.ie': 'none'})
    assert certbot == []
    assert 'WARNING: www.mytreatplan.ie does not resolve to this host' in result.stderr
    assert 'WARNING: mytreatplan.ie does not resolve to this host' in result.stderr
    assert 'mytreatplan.es does not resolve' not in result.stderr
    assert 'skipping the certificate' in result.stderr


# --- PostgreSQL client tools from PGDG (epic-platform entry 14) --------------------------

PGDG_FINGERPRINT = 'B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8'
OTHER_FINGERPRINT = '0000000000000000000000000000000000000000'
PGDG_KEY_OK = [('pub', PGDG_FINGERPRINT, '-'), ('sub', '1111111111111111111111111111111111111111', '-')]


def _provision_setting(name):
    return re.search(rf'^{name}=(.*)$', (DEPLOY / 'provision.sh').read_text(), re.M).group(1)


def test_provision_pins_postgresql_client_18_and_the_pgdg_key():
    script = (DEPLOY / 'provision.sh').read_text()
    packages = re.search(r'^PACKAGES=\((.*?)\)', script, re.S | re.M).group(1).split()
    assert 'gpg' in packages
    # No metapackage: it would follow PGDG's newest major; later majors come deliberately.
    assert 'postgresql-client' not in packages
    assert _provision_setting('PG_CLIENT_PACKAGES') == '(postgresql-client-18)'
    assert _provision_setting('PGDG_KEY_FINGERPRINT') == PGDG_FINGERPRINT
    assert _provision_setting('PGDG_KEY_URL') == 'https://www.postgresql.org/media/keys/ACCC4CF8.asc'
    assert _provision_setting('PGDG_URI') == 'https://apt.postgresql.org/pub/repos/apt'
    assert _provision_setting('PGDG_PREFERENCES') == '/etc/apt/preferences.d/mytp-pgdg'
    # The PGDG step is its own block, between the packages and the client install.
    assert (script.index('step "Packages"') < script.index('# --- PostgreSQL apt repository (PGDG)')
            < script.index('# --- PostgreSQL client tools') < script.index('# --- Virus scanner'))


def _pgdg_steps(tmp_path, *, installed=(), key=PGDG_KEY_OK, download=True, garbled=False):
    """provision.sh's packages, PGDG and client steps alone (install_packages too), with
    sudo, apt-get, dpkg, dpkg-query, curl and gpg as executables logging to calls.log and
    every path in tmp_path. `installed`: packages dpkg reports installed; `key`: the
    downloaded key's (pub|sub, fingerprint, validity) rows, as gpg --with-colons
    --show-keys lists them (the stub refuses other calls); `garbled`: the download is not
    a key (gpg fails); `download`: curl succeeds. Calling it again on the same tmp_path is
    a re-run. Returns (result, calls of this run)."""
    bin_dir, root = tmp_path / 'bin', tmp_path / 'root'
    bin_dir.mkdir(exist_ok=True)
    root.mkdir(exist_ok=True)
    (root / 'installed').write_text(''.join(f'{p}\n' for p in installed))
    (root / 'download.asc').write_text(
        'garbage\n' if garbled else ''.join(f'{kind} {fpr} {validity}\n' for kind, fpr, validity in key))
    (root / 'os-release').write_text('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n'
                                     'VERSION_CODENAME=trixie\nID=debian\n')
    calls = root / 'calls.log'
    calls.write_text('')
    q = shlex.quote
    stubs = {
        'sudo': 'exec env "$@"',
        'apt-get': f'echo "apt-get $*" >>{q(str(calls))}',
        'dpkg': '[ "$1" = --print-architecture ] && echo amd64',
        'dpkg-query': f'grep -qx -- "${{@: -1}}" {q(str(root / "installed"))} '
                      '&& printf "install ok installed"; exit 0',
        'curl': (f'echo "curl $*" >>{q(str(calls))}\n'
                 + ('' if download else 'exit 22\n')
                 + 'while [ $# -gt 1 ]; do [ "$1" = -o ] && out=$2; shift; done\n'
                 f'cp {q(str(root / "download.asc"))} "$out"'),
        # Only the listing provision.sh asks for: --with-colons --show-keys FILE.
        'gpg': ('[[ " $* " == *" --with-colons "* && " $* " == *" --show-keys "* ]] || exit 2\n'
                'f=${@: -1}\n'
                'while read -r kind fpr validity; do\n'
                '    case $kind in\n'
                '        pub|sub) echo "$kind:$validity:4096:1:X::::::::"; echo "fpr:::::::::$fpr:" ;;\n'
                '        *) echo "gpg: no valid OpenPGP data found." >&2; exit 2 ;;\n'
                '    esac\n'
                'done <"$f"'),
    }
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text(f'#!/bin/bash\n{body}\n')
        stub.chmod(0o755)
    script = (DEPLOY / 'provision.sh').read_text()
    block = script[script.index('# install_packages PKG...'):script.index('# --- Virus scanner')]
    prelude = f'''set -euo pipefail
export PATH={q(str(bin_dir))}:"$PATH"
step() {{ echo "==> $*"; }}
changed() {{ CHANGES=$((CHANGES + 1)); echo "  changed: $*"; }}
install_file() {{ cmp -s "$1" "$2" 2>/dev/null && return 1; cp "$1" "$2"; changed "$2"; }}
ensure_dir() {{ mkdir -p "$1"; }}
CHANGES=0
WORK={q(str(root / 'work'))}
rm -rf "$WORK" && mkdir "$WORK"
PACKAGES=(nginx gpg)
PG_CLIENT_PACKAGES={_provision_setting('PG_CLIENT_PACKAGES')}
PGDG_KEY_URL={_provision_setting('PGDG_KEY_URL')}
PGDG_KEY_FINGERPRINT={_provision_setting('PGDG_KEY_FINGERPRINT')}
PGDG_URI={_provision_setting('PGDG_URI')}
PGDG_KEY={q(str(root / 'keyrings' / 'mytp-pgdg.asc'))}
PGDG_SOURCES={q(str(root / 'mytp-pgdg.sources'))}
PGDG_PREFERENCES={q(str(root / 'mytp-pgdg.pref'))}
OS_RELEASE={q(str(root / 'os-release'))}
'''
    result = subprocess.run(['bash', '-c', prelude + block + 'echo "CHANGES=$CHANGES"\n'],
                            capture_output=True, text=True)
    return result, calls.read_text().splitlines()


def _apt(calls):
    return [c for c in calls if c.startswith('apt-get')]


def _stanzas(text):
    """The deb822 / apt preferences stanzas of `text` (comments dropped), as dicts."""
    body = '\n'.join(line for line in text.splitlines() if not line.startswith('#'))
    return [dict(line.split(': ', 1) for line in stanza.strip().splitlines())
            for stanza in body.strip().split('\n\n')]


@bash_only
def test_pgdg_first_run_adds_the_source_and_installs_client_18(tmp_path):
    result, calls = _pgdg_steps(tmp_path, installed=['nginx', 'gpg'])
    assert result.returncode == 0, result.stderr
    assert calls[0].startswith('curl -fsSL --proto =https --tlsv1.2 ')
    assert calls[0].endswith(' https://www.postgresql.org/media/keys/ACCC4CF8.asc')
    # One update, after the source is in place, then the explicit client package.
    assert _apt(calls) == ['apt-get update -q', 'apt-get install -y -q postgresql-client-18']
    root = tmp_path / 'root'
    key = root / 'keyrings' / 'mytp-pgdg.asc'
    assert key.read_text() == (root / 'download.asc').read_text()
    assert _stanzas((root / 'mytp-pgdg.sources').read_text()) == [{
        'Types': 'deb', 'URIs': 'https://apt.postgresql.org/pub/repos/apt',
        'Suites': 'trixie-pgdg', 'Components': 'main', 'Architectures': 'amd64',
        'Signed-By': str(key)}]
    assert 'CHANGES=4' in result.stdout     # key, sources, preferences, package


@bash_only
def test_pgdg_preferences_keep_everything_else_below_debian(tmp_path):
    result, _ = _pgdg_steps(tmp_path, installed=['nginx', 'gpg'])
    assert result.returncode == 0, result.stderr
    general, client = _stanzas((tmp_path / 'root' / 'mytp-pgdg.pref').read_text())
    assert general == {'Package': '*', 'Pin': 'origin apt.postgresql.org', 'Pin-Priority': '100'}
    # The client and what it needs at PGDG's versions (libpq5 18) at Debian's priority.
    assert client == {'Package': 'postgresql-client-18 postgresql-client-common libpq5',
                      'Pin': 'origin apt.postgresql.org', 'Pin-Priority': '500'}


@bash_only
def test_pgdg_rerun_changes_nothing(tmp_path):
    _pgdg_steps(tmp_path, installed=['nginx', 'gpg'])
    result, calls = _pgdg_steps(tmp_path, installed=['nginx', 'gpg', 'postgresql-client-18'])
    assert result.returncode == 0, result.stderr
    assert _apt(calls) == []
    assert 'changed' not in result.stdout and 'CHANGES=0' in result.stdout


@bash_only
def test_pgdg_new_base_package_updates_once(tmp_path):
    _pgdg_steps(tmp_path, installed=['nginx', 'gpg'])
    result, calls = _pgdg_steps(tmp_path, installed=['gpg', 'postgresql-client-18'])
    assert result.returncode == 0, result.stderr
    assert _apt(calls) == ['apt-get update -q', 'apt-get install -y -q nginx']


@bash_only
@pytest.mark.parametrize('name,old,new', [
    ('mytp-pgdg.sources', 'trixie-pgdg', 'bookworm-pgdg'),
    ('mytp-pgdg.pref', 'Pin-Priority: 100', 'Pin-Priority: 990'),
])
def test_pgdg_a_changed_file_alone_runs_apt_update(tmp_path, name, old, new):
    _pgdg_steps(tmp_path, installed=['nginx', 'gpg'])
    path = tmp_path / 'root' / name
    path.write_text(path.read_text().replace(old, new))
    result, calls = _pgdg_steps(tmp_path, installed=['nginx', 'gpg', 'postgresql-client-18'])
    assert result.returncode == 0, result.stderr
    assert _apt(calls) == ['apt-get update -q']
    assert old in path.read_text() and new not in path.read_text()


@bash_only
@pytest.mark.parametrize('key', [
    [('pub', OTHER_FINGERPRINT, '-')],
    [('pub', PGDG_FINGERPRINT, '-'), ('pub', OTHER_FINGERPRINT, '-')],
    [('pub', OTHER_FINGERPRINT, '-'), ('sub', PGDG_FINGERPRINT, '-')],
    [],
])
def test_pgdg_refuses_a_key_without_the_pinned_fingerprint(tmp_path, key):
    result, calls = _pgdg_steps(tmp_path, installed=['nginx', 'gpg'], key=key)
    assert result.returncode == 1
    assert 'not the pinned PGDG key' in result.stderr and PGDG_FINGERPRINT in result.stderr
    assert _apt(calls) == []
    root = tmp_path / 'root'
    assert not (root / 'keyrings' / 'mytp-pgdg.asc').exists()
    assert not (root / 'mytp-pgdg.sources').exists()
    assert not (root / 'mytp-pgdg.pref').exists()


@bash_only
@pytest.mark.parametrize('validity,word', [('e', 'expired'), ('r', 'revoked')])
def test_pgdg_refuses_an_expired_or_revoked_key(tmp_path, validity, word):
    _pgdg_steps(tmp_path, installed=['nginx', 'gpg'])
    installed_key = (tmp_path / 'root' / 'keyrings' / 'mytp-pgdg.asc').read_text()
    result, calls = _pgdg_steps(tmp_path, installed=['nginx', 'gpg', 'postgresql-client-18'],
                                key=[('pub', PGDG_FINGERPRINT, validity)])
    assert result.returncode == 1
    assert 'ERROR: the PGDG key at' in result.stderr and f'is {word}' in result.stderr
    assert _apt(calls) == []
    # The installed key is left as it was.
    assert (tmp_path / 'root' / 'keyrings' / 'mytp-pgdg.asc').read_text() == installed_key


@bash_only
def test_pgdg_a_garbled_download_stops_and_keeps_the_installed_key(tmp_path):
    """Unlike a failed download (kept with a WARNING), a download that is not the pinned
    key stops the run: it may be tampered with, so nothing is installed or updated."""
    _pgdg_steps(tmp_path, installed=['nginx', 'gpg'])
    installed_key = (tmp_path / 'root' / 'keyrings' / 'mytp-pgdg.asc').read_text()
    result, calls = _pgdg_steps(tmp_path, installed=['nginx', 'gpg', 'postgresql-client-18'],
                                garbled=True)
    assert result.returncode == 1
    assert 'not the pinned PGDG key' in result.stderr
    assert _apt(calls) == []
    assert (tmp_path / 'root' / 'keyrings' / 'mytp-pgdg.asc').read_text() == installed_key


@bash_only
def test_pgdg_keeps_the_installed_key_when_the_download_fails(tmp_path):
    _pgdg_steps(tmp_path, installed=['nginx', 'gpg'])
    result, calls = _pgdg_steps(tmp_path, installed=['nginx', 'gpg', 'postgresql-client-18'],
                                download=False)
    assert result.returncode == 0, result.stderr
    assert 'WARNING: could not download' in result.stderr
    assert _apt(calls) == [] and 'CHANGES=0' in result.stdout


@bash_only
def test_pgdg_without_a_key_and_no_download_stops(tmp_path):
    result, calls = _pgdg_steps(tmp_path, installed=['nginx', 'gpg'], download=False)
    assert result.returncode == 1
    assert 'ERROR: could not download' in result.stderr
    assert _apt(calls) == []
    assert not (tmp_path / 'root' / 'mytp-pgdg.sources').exists()
