"""The sample page in Chromium against live_server, with the bucket emulated by Playwright
routes (no network): the vendored Uppy 6 loads, every S3 request is signed by the site
first, every file is S3 multipart in 16 MiB parts (a 20 MiB file has 2), the site records
each completed upload as a StoredObject with its name, and a part refused once with 403
is retried and resumed. No file name reaches the bucket or the sign endpoint."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip('pytest_playwright')

from django.test import Client  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from accounts.models import User  # noqa: E402
from storage import s3  # noqa: E402
from storage.models import StoredObject  # noqa: E402

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

BUCKET_URL = 'https://mytp-dev-files.s3.gra.io.cloud.ovh.net'
MiB = 1024 * 1024
XMLNS = 'http://s3.amazonaws.com/doc/2006-03-01/'


class FakeBucket:
    """Answers the presigned requests as S3 would, with the CORS headers the bucket gets
    from `storage_bucket --apply`. `refuse_part` makes that part number's first PUT a 403."""

    def __init__(self, origin, refuse_part=None):
        self.origin = origin
        self.calls = []
        self.parts = {}
        self.refuse_part = refuse_part
        self.refused = False

    def cors(self):
        return {'Access-Control-Allow-Origin': self.origin,
                'Access-Control-Allow-Methods': 'GET, PUT, POST, DELETE',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Expose-Headers': 'ETag'}

    def size(self, key):
        return sum(self.parts.get(key, {}).values())

    def xml(self, route, body):
        route.fulfill(status=200, headers={**self.cors(), 'Content-Type': 'application/xml'},
                      body=f'<?xml version="1.0" encoding="UTF-8"?>{body}')

    def handle(self, route, request):
        url = urlsplit(request.url)
        query = parse_qs(url.query, keep_blank_values=True)
        key = url.path.lstrip('/')
        assert 'X-Amz-Signature' in query or request.method == 'OPTIONS'
        part = query.get('partNumber', [''])[0]
        self.calls.append((request.method, key, part, 'uploads' in query,
                           query.get('uploadId', [''])[0]))
        if request.method == 'OPTIONS':
            route.fulfill(status=204, headers=self.cors())
        elif request.method == 'POST' and 'uploads' in query:
            self.xml(route, f'<InitiateMultipartUploadResult xmlns="{XMLNS}"><Bucket>mytp-dev-files'
                            f'</Bucket><Key>{key}</Key><UploadId>up-1</UploadId>'
                            '</InitiateMultipartUploadResult>')
        elif request.method == 'PUT':
            if part == str(self.refuse_part) and not self.refused:
                self.refused = True
                route.fulfill(status=403, headers={**self.cors(), 'Content-Type': 'application/xml'},
                              body='<Error><Code>AccessDenied</Code></Error>')
                return
            self.parts.setdefault(key, {})[int(part)] = len(request.post_data_buffer or b'')
            route.fulfill(status=200, headers={**self.cors(), 'ETag': f'"etag-{part}"'})
        elif request.method == 'GET' and 'uploadId' in query:
            parts = ''.join(f'<Part><PartNumber>{n}</PartNumber><ETag>"etag-{n}"</ETag>'
                            f'<Size>{size}</Size></Part>'
                            for n, size in sorted(self.parts.get(key, {}).items()))
            self.xml(route, f'<ListPartsResult xmlns="{XMLNS}"><Bucket>mytp-dev-files</Bucket>'
                            f'<Key>{key}</Key><UploadId>up-1</UploadId>{parts}</ListPartsResult>')
        elif request.method == 'POST' and 'uploadId' in query:
            self.xml(route, f'<CompleteMultipartUploadResult xmlns="{XMLNS}"><Location>'
                            f'{BUCKET_URL}/{key}</Location><Bucket>mytp-dev-files</Bucket>'
                            f'<Key>{key}</Key><ETag>"final-2"</ETag></CompleteMultipartUploadResult>')
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


def upload(page, live_server, file):
    page.goto(f'{live_server.url}/en/storage/sample/')
    log = page.locator('#storage-log')
    expect(log).to_contain_text('ready (Uppy 6.')
    page.locator('input.uppy-Dashboard-input').first.set_input_files([file])
    page.locator('.uppy-StatusBar-actionBtn--upload').click()
    expect(log).to_contain_text('stored ', timeout=30_000)
    return log


def watch_sign_bodies(page):
    bodies = []
    page.on('request', lambda request: bodies.append(json.loads(request.post_data))
            if urlsplit(request.url).path in ('/storage/sign/', '/storage/complete/') else None)
    return bodies


def test_small_and_two_part_uploads_are_stored(live_server, staff_page, monkeypatch):
    page = staff_page
    bucket = FakeBucket(live_server.url)
    page.route(f'{BUCKET_URL}/**', bucket.handle)
    bodies = watch_sign_bodies(page)
    monkeypatch.setattr(s3, 'head_size', lambda key, client=None: bucket.size(key))

    # One file per page load: the test database is SQLite, which locks on concurrent
    # session writes (dev's PostgreSQL does not), and a fresh Dashboard keeps it simple.
    for file, size in [
        ({'name': 'small-note.txt', 'mimeType': 'text/plain', 'buffer': b'x' * 1024}, 1024),
        ({'name': 'patient-scan.bin', 'mimeType': 'application/octet-stream',
          'buffer': b'y' * (20 * MiB)}, 20 * MiB),
    ]:
        log = upload(page, live_server, file)
        expect(log).to_contain_text(f': {size} bytes (matches)')

    keys = {key for _method, key, *_ in bucket.calls}
    assert len(keys) == 2 and all(s3.object_id_from_key(key) for key in keys)
    # Multipart for both; the 20 MiB file in parts of 16 MiB and 4 MiB.
    assert sorted(bucket.parts[k][n] for k in keys for n in bucket.parts[k]) == [
        1024, 4 * MiB, 16 * MiB]
    stored = {str(o.pk): o for o in StoredObject.objects.all()}
    assert {f'quarantine/{pk}' for pk in stored} == keys
    assert sorted(o.original_name for o in stored.values()) == ['patient-scan.bin', 'small-note.txt']
    assert {o.purpose for o in stored.values()} == {'sample'}
    # Starting declared purpose and size; the file name went only to /storage/complete/.
    starts = [b for b in bodies if b.get('method') == 'POST' and 'purpose' in b]
    assert sorted(b['size'] for b in starts) == [1024, 20 * MiB]
    signs = [b for b in bodies if 'method' in b]
    assert {b['method'] for b in signs} == {'PUT', 'POST'}
    for text in [json.dumps(signs), json.dumps(bucket.calls)]:
        assert 'small-note' not in text and 'patient-scan' not in text


def test_a_part_refused_with_403_is_retried_once_and_resumed(live_server, staff_page,
                                                            monkeypatch):
    page = staff_page
    bucket = FakeBucket(live_server.url, refuse_part=2)
    page.route(f'{BUCKET_URL}/**', bucket.handle)
    monkeypatch.setattr(s3, 'head_size', lambda key, client=None: bucket.size(key))

    log = upload(page, live_server, {'name': 'retry.bin', 'mimeType': 'application/octet-stream',
                                     'buffer': b'z' * (20 * MiB)})
    expect(log).to_contain_text(f': {20 * MiB} bytes (matches)')
    expect(log).to_contain_text('retrying retry.bin once after a 403')

    assert bucket.refused
    creates = [c for c in bucket.calls if c[0] == 'POST' and c[3]]
    assert len(creates) == 1  # resumed (ListParts), not restarted
    assert StoredObject.objects.count() == 1
