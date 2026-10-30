"""The spike page in Chromium against live_server, with the bucket emulated by Playwright
routes (no network): the vendored Uppy 6 loads, every S3 request is signed by the site
first, a small file takes one presigned PUT and a 12 MiB file S3 multipart (3 parts),
and each stored object is listed with its key, size and ETag. No file name reaches
the site or the bucket."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip('pytest_playwright')

from django.test import Client  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from accounts.models import User  # noqa: E402
from upload_spike import s3  # noqa: E402

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

BUCKET_URL = 'https://mytp-dev-files.s3.gra.io.cloud.ovh.net'
MiB = 1024 * 1024
XMLNS = 'http://s3.amazonaws.com/doc/2006-03-01/'


class FakeBucket:
    """Answers the presigned requests as S3 would, with the CORS headers the spike
    bucket gets from `upload_spike_bucket --apply`."""

    def __init__(self, origin):
        self.origin = origin
        self.calls = []
        self.sizes = {}

    def cors(self):
        return {'Access-Control-Allow-Origin': self.origin,
                'Access-Control-Allow-Methods': 'GET, PUT, POST, DELETE',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Expose-Headers': 'ETag'}

    def xml(self, route, body):
        route.fulfill(status=200, headers={**self.cors(), 'Content-Type': 'application/xml'},
                      body=f'<?xml version="1.0" encoding="UTF-8"?>{body}')

    def handle(self, route, request):
        url = urlsplit(request.url)
        query = parse_qs(url.query, keep_blank_values=True)
        key = url.path.lstrip('/')
        assert 'X-Amz-Signature' in query or request.method == 'OPTIONS'
        self.calls.append((request.method, key, query.get('partNumber', [''])[0],
                           'uploads' in query, query.get('uploadId', [''])[0]))
        if request.method == 'OPTIONS':
            route.fulfill(status=204, headers=self.cors())
        elif request.method == 'POST' and 'uploads' in query:
            self.xml(route, f'<InitiateMultipartUploadResult xmlns="{XMLNS}"><Bucket>mytp-dev-files'
                            f'</Bucket><Key>{key}</Key><UploadId>up-1</UploadId>'
                            '</InitiateMultipartUploadResult>')
        elif request.method == 'PUT':
            self.sizes[key] = self.sizes.get(key, 0) + len(request.post_data_buffer or b'')
            part = query.get('partNumber', ['0'])[0]
            route.fulfill(status=200, headers={**self.cors(), 'ETag': f'"etag-{part}"'})
        elif request.method == 'POST' and 'uploadId' in query:
            self.xml(route, f'<CompleteMultipartUploadResult xmlns="{XMLNS}"><Location>'
                            f'{BUCKET_URL}/{key}</Location><Bucket>mytp-dev-files</Bucket>'
                            f'<Key>{key}</Key><ETag>"final-3"</ETag></CompleteMultipartUploadResult>')
        else:
            route.fulfill(status=400, headers=self.cors(), body='unexpected')


@pytest.fixture
def staff_page(live_server, page):
    staff = User.objects.create_user('staff@example.net', 'a-long-test-password-9', is_staff=True)
    client = Client()
    client.force_login(staff)
    from django.conf import settings
    session = client.cookies[settings.SESSION_COOKIE_NAME].value
    page.context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': session,
                               'url': live_server.url}])
    return page


def test_small_put_and_multipart_upload(live_server, staff_page, monkeypatch):
    page = staff_page
    bucket = FakeBucket(live_server.url)
    page.route(f'{BUCKET_URL}/**', bucket.handle)
    sign_bodies = []
    page.on('request', lambda request: sign_bodies.append(json.loads(request.post_data))
            if request.url.endswith('/upload-spike/sign/') else None)
    monkeypatch.setattr(s3, 'describe', lambda key, client=None: {
        'key': key, 'size': bucket.sizes.get(key), 'etag': 'from-head',
        'serverSideEncryption': 'AES256', 'downloadUrl': f'{BUCKET_URL}/{key}?X-Amz-Signature=x'})

    # One file per page load: the test database is SQLite, which locks on concurrent
    # session writes (dev's PostgreSQL does not), and a fresh Dashboard keeps it simple.
    for file, size in [
        ({'name': 'small-note.txt', 'mimeType': 'text/plain', 'buffer': b'x' * 1024}, 1024),
        ({'name': 'patient-scan.bin', 'mimeType': 'application/octet-stream',
          'buffer': b'y' * (12 * MiB)}, 12 * MiB),
    ]:
        page.goto(f'{live_server.url}/en/upload-spike/')
        expect(page.locator('#upload-spike-log')).to_contain_text('ready (Uppy 6.')
        page.locator('input.uppy-Dashboard-input').first.set_input_files([file])
        page.locator('.uppy-StatusBar-actionBtn--upload').click()
        result = page.locator('#upload-spike-results li')
        expect(result).to_have_count(1, timeout=30_000)
        expect(result).to_contain_text(f'Size: {size} bytes (matches)')
        expect(result).to_contain_text('ETag: from-head')
        expect(result).to_contain_text('Encryption: AES256')
        expect(result).to_contain_text('Key: spike/')

    keys = {key for _method, key, *_ in bucket.calls}
    assert len(keys) == 2 and all(s3.is_spike_key(key) for key in keys)
    parts = sorted(int(part) for method, _k, part, _u, _id in bucket.calls
                   if method == 'PUT' and part)
    assert parts == [1, 2, 3]
    assert any(method == 'POST' and uploads for method, _k, _p, uploads, _id in bucket.calls)
    assert any(method == 'POST' and upload_id == 'up-1'
               for method, _k, _p, _u, upload_id in bucket.calls)
    # Every S3 request was signed by the site; no file name went to the site or the bucket.
    assert {body['method'] for body in sign_bodies} == {'PUT', 'POST'}
    for text in [json.dumps(sign_bodies), json.dumps(bucket.calls)]:
        assert 'small-note' not in text and 'patient-scan' not in text
