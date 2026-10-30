import pytest

# Fake, non-secret bucket settings: a developer's settings.env must never carry real
# bucket keys into a test. Nothing here reaches the network (presigning is local).
FAKE_ACCESS_KEY = 'SPIKEACCESSKEYTEST01'
FAKE_SECRET_KEY = 'spike-secret-key-for-tests-only-0123456789'


@pytest.fixture(autouse=True)
def spike_settings(settings):
    settings.UPLOAD_SPIKE_ENABLED = True
    settings.UPLOAD_SPIKE_S3_ENDPOINT = 'https://s3.gra.io.cloud.ovh.net'
    settings.UPLOAD_SPIKE_S3_REGION = 'gra'
    settings.UPLOAD_SPIKE_S3_BUCKET = 'mytp-dev-files'
    settings.UPLOAD_SPIKE_S3_ACCESS_KEY = FAKE_ACCESS_KEY
    settings.UPLOAD_SPIKE_S3_SECRET_KEY = FAKE_SECRET_KEY
    settings.UPLOAD_SPIKE_CORS_ORIGINS = ['https://dev.mytpdso.com']
    settings.ROOT_URLCONF = 'upload_spike.tests.urls_spike'
    return settings
