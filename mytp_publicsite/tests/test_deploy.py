"""Story 1.13: staging provisioning and deploy pipeline. Static checks of deploy/ and
the deploy-staging CI job (the scripts themselves run only on the server)."""
import re
import shutil
import subprocess
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
    assert 'release.sh' in commands


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
        line for line in (DEPLOY / 'sudoers' / 'mytp-deploy').read_text().splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    assert rules == ['mytp ALL=(root) NOPASSWD: /usr/bin/systemctl restart mytp-gunicorn']


def test_nginx_template_auth_redirect_and_proxy_headers():
    conf = (DEPLOY / 'nginx' / 'mytp.conf.template').read_text()
    assert 'server_name __DOMAIN__;' in conf
    assert 'return 301 https://__DOMAIN__$request_uri;' in conf
    assert '$host$request_uri' not in conf
    assert 'proxy_set_header X-Forwarded-Proto $scheme;' in conf
    assert 'auth_basic_user_file /etc/nginx/mytp.htpasswd;' in conf
    assert 'alias /srv/mytp/current/static_root/;' in conf
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
    assert values['DB_SSLROOTCERT'] == '/srv/mytp/shared/db-ca.pem'
