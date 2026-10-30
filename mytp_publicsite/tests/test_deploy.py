"""Story 1.13: staging provisioning and deploy pipeline. Static checks of deploy/ and
the deploy-staging CI job (the scripts themselves run only on the server)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings

REPO = Path(settings.BASE_DIR)
DEPLOY = REPO / 'deploy'
WORKFLOW = REPO / '.github' / 'workflows' / 'ci.yml'
SCRIPTS = sorted(DEPLOY.glob('*.sh'))


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


def test_sudoers_allows_only_the_app_restart():
    rules = [
        line for line in (DEPLOY / 'sudoers' / 'deploy.template').read_text().splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    assert rules == [
        'mytp-__SITE__ ALL=(root) NOPASSWD: /usr/bin/systemctl restart mytp-__SITE__-gunicorn']


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


# --- Site-aware deploy tooling (epic-platform entry 1) ---------------------------------

SITE_TEMPLATES = [
    DEPLOY / 'sudoers' / 'deploy.template',
    DEPLOY / 'systemd' / 'gunicorn.socket.template',
    DEPLOY / 'systemd' / 'gunicorn.service.template',
    DEPLOY / 'systemd' / 'job@.service.template',
    DEPLOY / 'systemd' / 'job.timer.template',
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
    REPO / 'mytreatplan' / 'settings.py', REPO / 'mytp_publicsite' / 'jobs.py',
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
    from mytp_publicsite import jobs

    assert settings.SITE_HOME == Path('/srv') / f'mytp-{settings.SITE}'
    assert re.fullmatch(SITE_RE[1:-1], settings.SITE)
    text = (REPO / 'mytp_publicsite' / 'jobs.py').read_text()
    assert "INSTALLED_JOBS_FILE = Path(settings.SITE_HOME) / 'shared' / 'jobs.conf'" in text
    assert jobs.JOBS_FILE.is_file()


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


bash_only = pytest.mark.skipif(shutil.which('bash') is None, reason='bash not available')


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
