"""Virus scanning of uploads (epic-platform entry 12; AD-9, E6): the INSTREAM client
(storage.clamd) against a fake clamd on a unix socket, and scan_object_task with the
bucket in a botocore Stubber: clean files move to `clean/`, everything else is rejected
(deleted, row kept, system DeletionTrace), clamd down retries, a rerun changes nothing,
and only the object's UUID and the outcome code reach logs and Sentry."""
import io
import logging
import os
import shutil
import socket
import struct
import tempfile
import threading
import uuid
from datetime import timedelta

import pytest
from botocore.response import StreamingBody
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from storage import clamd, checks, s3, service, tasks
from storage.models import DeletionTrace, StoredObject

from .conftest import BUCKET, MiB, TEST_PURPOSE

NAME = 'Ruiz implant plan.pdf'
EICAR = (rb'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*')
EICAR_SIGNATURE = 'Win.Test.EICAR_HDB-1'


# --- A fake clamd ------------------------------------------------------------------------

class FakeClamd:
    """clamd on a unix socket in a thread: reads one INSTREAM (or VERSION) per connection
    and answers `reply`. `reply` may be a callable of the received data; `close_after`
    stops reading after that many data bytes, answers and closes (StreamMaxLength);
    `reply=None` closes without an answer."""

    def __init__(self, path):
        self.path = path
        self.reply = b'stream: OK\0'
        self.close_after = None
        self._raw = []          # per connection: the raw pieces read, before any answer
        self.data = []          # the data each INSTREAM carried (before its answer)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(5)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.running = True
        self.thread.start()

    def _recv_exact(self, conn, n, raw):
        buf = b''
        while len(buf) < n:
            part = conn.recv(n - len(buf))
            if not part:
                raise ConnectionError('client closed')
            buf += part
        raw.append(buf)
        return buf

    def _serve(self):
        while self.running:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                raw = []
                self._raw.append(raw)
                try:
                    self._handle(conn, raw)
                except (ConnectionError, OSError):
                    pass

    @property
    def received(self):
        """The raw bytes of each connection."""
        return [b''.join(raw) for raw in self._raw]

    def _answer(self, conn, data):
        reply = self.reply(data) if callable(self.reply) else self.reply
        if reply is not None:
            conn.sendall(reply)

    def _handle(self, conn, raw):
        command = b''
        while not command.endswith(b'\0'):
            command += self._recv_exact(conn, 1, raw)
        if command == b'zVERSION\0':
            self._answer(conn, b'')
            return
        assert command == b'zINSTREAM\0', command
        data = b''
        while True:
            (length,) = struct.unpack('!L', self._recv_exact(conn, 4, raw))
            if length == 0:
                break
            data += self._recv_exact(conn, length, raw)
            if self.close_after is not None and len(data) > self.close_after:
                self.data.append(data)
                self._answer(conn, data)
                conn.shutdown(socket.SHUT_RDWR)
                return
        self.data.append(data)
        self._answer(conn, data)

    def stop(self):
        self.running = False
        self.server.close()


@pytest.fixture
def fake_clamd(settings):
    # A short path: unix socket paths are limited to ~100 bytes (macOS's temp dir is long).
    base = '/tmp' if os.access('/tmp', os.W_OK) else None
    folder = tempfile.mkdtemp(prefix='clamd', dir=base)
    fake = FakeClamd(os.path.join(folder, 'clamd.ctl'))
    settings.STORAGE_CLAMD_SOCKET = fake.path
    yield fake
    fake.stop()
    shutil.rmtree(folder, ignore_errors=True)


# --- The INSTREAM client -----------------------------------------------------------------

def test_instream_framing(fake_clamd):
    verdict = clamd.scan_stream([b'abc', b'', b'de'], socket_path=fake_clamd.path)

    assert verdict == clamd.Verdict(clamd.OK) and verdict.code == 'clean'
    (raw,) = fake_clamd.received
    assert raw == (b'zINSTREAM\0' + struct.pack('!L', 3) + b'abc' + struct.pack('!L', 2)
                   + b'de' + struct.pack('!L', 0))


def test_large_pieces_go_in_chunks_of_at_most_1_mib(fake_clamd):
    data = os.urandom(clamd.CHUNK_SIZE * 2 + 5)
    assert clamd.scan_stream([data], socket_path=fake_clamd.path).result == clamd.OK
    assert fake_clamd.data == [data]
    raw = fake_clamd.received[0][len(clamd.COMMAND):]
    lengths = []
    while raw:
        (length,) = struct.unpack('!L', raw[:4])
        lengths.append(length)
        raw = raw[4 + length:]
    assert lengths == [clamd.CHUNK_SIZE, clamd.CHUNK_SIZE, 5, 0]


@pytest.mark.parametrize('reply, result, signature, code', [
    (b'stream: OK\0', clamd.OK, '', 'clean'),
    (b'stream: Win.Test.EICAR_HDB-1 FOUND\0', clamd.FOUND, EICAR_SIGNATURE, 'infected'),
    (b'stream: Heuristics.Limits.Exceeded.MaxFileSize FOUND\0', clamd.FOUND,
     'Heuristics.Limits.Exceeded.MaxFileSize', 'limit_exceeded'),
    (b'stream: Heuristics.Encrypted.Zip FOUND\0', clamd.FOUND, 'Heuristics.Encrypted.Zip',
     'unscannable'),
    (b'stream: Heuristics.Broken.Media.JPEG.EOFReadingSOI FOUND\0', clamd.FOUND,
     'Heuristics.Broken.Media.JPEG.EOFReadingSOI', 'unscannable'),
    (b'INSTREAM size limit exceeded. ERROR\0', clamd.ERROR, '', 'limit_exceeded'),
    (b"stream: Can't allocate memory ERROR\0", clamd.ERROR, '', 'scan_error'),
    (b'stream: OK but not quite\0', clamd.ERROR, '', 'scan_error'),
    (b'UNKNOWN COMMAND\n', clamd.ERROR, '', 'scan_error'),
    (b'stream: /etc/passwd <script> FOUND\0', clamd.FOUND, 'unknown', 'infected'),
])
def test_replies(reply, result, signature, code):
    verdict = clamd.parse_reply(reply)
    assert (verdict.result, verdict.signature, verdict.code) == (result, signature, code)


def test_clamd_closing_mid_stream_answers_with_its_error(fake_clamd):
    """Over StreamMaxLength clamd answers and closes while the data is still coming."""
    fake_clamd.close_after = 2 * MiB
    fake_clamd.reply = b'INSTREAM size limit exceeded. ERROR\0'
    chunks = (os.urandom(MiB) for _ in range(64))

    verdict = clamd.scan_stream(chunks, socket_path=fake_clamd.path)

    assert verdict.result == clamd.ERROR and verdict.code == 'limit_exceeded'


@pytest.mark.parametrize('reply', [b'stream: OK\0', b'garbage\0'])
def test_an_early_answer_before_the_end_is_never_ok(fake_clamd, reply):
    """clamd answering before the zero chunk has not seen the whole file: anything but a
    FOUND or an ERROR is a scan_error, an early "OK" included."""
    fake_clamd.close_after = 2 * MiB
    fake_clamd.reply = reply
    chunks = (os.urandom(MiB) for _ in range(64))

    verdict = clamd.scan_stream(chunks, socket_path=fake_clamd.path)

    assert verdict.result == clamd.ERROR and verdict.code == 'scan_error'


def test_an_early_found_stands(fake_clamd):
    fake_clamd.close_after = 2 * MiB
    fake_clamd.reply = b'stream: Win.Test.EICAR_HDB-1 FOUND\0'
    verdict = clamd.scan_stream((os.urandom(MiB) for _ in range(64)),
                                socket_path=fake_clamd.path)
    assert verdict.code == 'infected'


def test_no_socket_is_unavailable(tmp_path):
    with pytest.raises(clamd.ClamdUnavailable):
        clamd.scan_stream([b'x'], socket_path=str(tmp_path / 'none.ctl'))
    with pytest.raises(clamd.ClamdUnavailable):
        clamd.scan_stream([b'x'], socket_path='')


def test_no_answer_is_unavailable(fake_clamd):
    fake_clamd.reply = None
    with pytest.raises(clamd.ClamdUnavailable):
        clamd.scan_stream([b'x'], socket_path=fake_clamd.path)


def test_a_source_error_propagates_without_waiting_for_clamd(fake_clamd):
    def chunks():
        yield b'abc'
        raise ConnectionResetError('S3 read failed')

    with pytest.raises(ConnectionResetError):
        clamd.scan_stream(chunks(), socket_path=fake_clamd.path, timeout=5)


def test_version_and_signature_date(fake_clamd):
    fake_clamd.reply = b'ClamAV 1.4.3/27450/Fri Nov  6 08:30:00 2026\0'
    text = clamd.version(socket_path=fake_clamd.path)
    assert text == 'ClamAV 1.4.3/27450/Fri Nov  6 08:30:00 2026'
    assert clamd.signature_date(text).isoformat() == '2026-11-06T08:30:00'
    assert clamd.signature_date('ClamAV 1.4.3') is None


# --- The scan task ---------------------------------------------------------------------

def stored(state=StoredObject.QUARANTINE, **kwargs):
    defaults = {'purpose': TEST_PURPOSE, 'size': 3, 'original_name': NAME, 'state': state}
    return StoredObject.objects.create(**{**defaults, **kwargs})


def body(data):
    return {'Body': StreamingBody(io.BytesIO(data), len(data)), 'ContentLength': len(data)}


def expect_get(stubber, obj, data):
    stubber.add_response('get_object', body(data),
                         {'Bucket': BUCKET, 'Key': f'quarantine/{obj.pk}'})


def expect_promotion(stubber, obj, *, clean_exists=False):
    clean = {'Bucket': BUCKET, 'Key': f'clean/{obj.pk}'}
    if clean_exists:
        stubber.add_response('head_object', {'ContentLength': obj.size}, clean)
    else:
        stubber.add_client_error('head_object', service_error_code='404', http_status_code=404,
                                 expected_params=clean)
        stubber.add_response('copy_object', {}, {
            **clean, 'CopySource': {'Bucket': BUCKET, 'Key': f'quarantine/{obj.pk}'}})
    stubber.add_response('delete_object', {}, {'Bucket': BUCKET, 'Key': f'quarantine/{obj.pk}'})


def expect_rejection(stubber, obj):
    for key in (f'quarantine/{obj.pk}', f'clean/{obj.pk}'):
        stubber.add_response('delete_object', {}, {'Bucket': BUCKET, 'Key': key})


def assert_nothing_sensitive(text, obj):
    assert NAME not in text and 'quarantine/' not in text and 'clean/' not in text
    assert BUCKET not in text and 'https://' not in text


@pytest.fixture
def reports(monkeypatch):
    """The messages the scan task sends to Sentry."""
    sent = []
    monkeypatch.setattr(tasks.sentry_sdk, 'capture_message',
                        lambda message, level=None: sent.append(message))
    return sent


@pytest.mark.django_db
def test_a_clean_file_moves_to_clean_and_becomes_downloadable(fake_clamd, stubber, doctor,
                                                               caplog):
    caplog.set_level(logging.DEBUG, logger='storage')
    data = os.urandom(3 * MiB + 17)   # (stands for the 1 GB file of the staging check)
    obj = stored(uploaded_by=doctor, size=len(data))
    expect_get(stubber, obj, data)
    expect_promotion(stubber, obj)

    assert tasks.scan_object_task.call(str(obj.pk)) == 'clean'

    obj.refresh_from_db()
    assert obj.state == StoredObject.CLEAN and obj.key == f'clean/{obj.pk}'
    assert fake_clamd.data == [data]
    assert not DeletionTrace.objects.exists()
    url = service.download_url(obj, doctor, 'test_reason')
    assert f'/clean/{obj.pk}?' in url
    assert_nothing_sensitive(caplog.text, obj)


@pytest.mark.django_db
def test_eicar_is_rejected_deleted_and_traced(fake_clamd, stubber, doctor, caplog):
    caplog.set_level(logging.DEBUG, logger='storage')
    fake_clamd.reply = lambda data: (b'stream: Win.Test.EICAR_HDB-1 FOUND\0' if EICAR in data
                                     else b'stream: OK\0')
    obj = stored(uploaded_by=doctor, size=len(EICAR))
    expect_get(stubber, obj, EICAR)
    expect_rejection(stubber, obj)

    assert tasks.scan_object_task.call(str(obj.pk)) == 'infected'

    obj.refresh_from_db()
    assert obj.state == StoredObject.REJECTED
    trace = DeletionTrace.objects.get()
    assert (trace.object_id, trace.actor_type, trace.actor_uuid) == (obj.pk, 'system', None)
    with pytest.raises(service.DownloadRefused):
        service.download_url(obj, doctor, 'test_reason')
    # The UUID, the outcome code and the signature name; never the name or a key.
    assert str(obj.pk) in caplog.text and 'infected' in caplog.text
    assert EICAR_SIGNATURE in caplog.text
    assert_nothing_sensitive(caplog.text, obj)


@pytest.mark.django_db
@pytest.mark.parametrize('reply, code', [
    (b'INSTREAM size limit exceeded. ERROR\0', 'limit_exceeded'),
    (b'stream: Heuristics.Limits.Exceeded.MaxScanSize FOUND\0', 'limit_exceeded'),
    (b'stream: Heuristics.Encrypted.Zip FOUND\0', 'unscannable'),
    (b'stream: Heuristics.Broken.Executable FOUND\0', 'unscannable'),
])
def test_files_clamd_cannot_fully_scan_are_rejected(fake_clamd, stubber, reply, code):
    fake_clamd.reply = reply
    obj = stored()
    expect_get(stubber, obj, b'PK\x03\x04 encrypted')
    expect_rejection(stubber, obj)

    assert tasks.scan_object_task.call(str(obj.pk)) == code
    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.REJECTED
    assert DeletionTrace.objects.filter(object_id=obj.pk, actor_type='system').exists()


@pytest.mark.django_db
def test_a_file_above_the_stream_limit_is_rejected(fake_clamd, stubber):
    """clamd stops reading past StreamMaxLength and answers ERROR: rejected, not retried."""
    fake_clamd.close_after = 2 * MiB
    fake_clamd.reply = b'INSTREAM size limit exceeded. ERROR\0'
    data = os.urandom(12 * MiB)
    obj = stored(size=len(data))
    expect_get(stubber, obj, data)
    expect_rejection(stubber, obj)

    assert tasks.scan_object_task.call(str(obj.pk)) == 'limit_exceeded'
    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.REJECTED


UNDECIDED_REPLIES = [
    b'stream: ERROR\0', b"stream: Can't allocate memory ERROR\0", b'stream: OK?\0',
    b'stream: OKAY\0', b'garbage', b'stream: \0']


@pytest.mark.django_db
@pytest.mark.parametrize('reply', UNDECIDED_REPLIES)
def test_other_errors_and_unknown_answers_are_retried_never_promoted(fake_clamd, stubber,
                                                                     reply, reports):
    """Acceptance: any answer but OK or FOUND never promotes. Decision 2026-11-08: an
    ERROR other than a size limit (or a garbled answer) decides nothing either: the
    object stays in quarantine and the scan is retried."""
    fake_clamd.reply = reply
    obj = stored()
    expect_get(stubber, obj, b'abc')   # no copy or delete: the Stubber fails on any other call

    assert tasks.scan_object_task.call(str(obj.pk)) == 'retry'

    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.QUARANTINE
    assert not DeletionTrace.objects.exists()
    retry = DBTaskResult.objects.get()
    assert retry.args_kwargs == {'args': [str(obj.pk), 2], 'kwargs': {}}
    assert reports == []


@pytest.mark.django_db
def test_a_scan_error_at_the_last_attempt_is_reported_once(fake_clamd, stubber, reports,
                                                          caplog):
    fake_clamd.reply = b"stream: Can't allocate memory ERROR\0"
    obj = stored()
    expect_get(stubber, obj, b'abc')

    assert tasks.scan_object_task.call(str(obj.pk), tasks.MAX_ATTEMPTS) == 'failed'

    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.QUARANTINE
    assert not DBTaskResult.objects.exists()
    (report,) = reports
    assert str(obj.pk) in report and '(scan_error)' in report
    assert 'allocate' not in report and 'allocate' not in caplog.text
    assert_nothing_sensitive(report, obj)


@pytest.mark.django_db
def test_an_early_ok_is_retried_not_promoted(fake_clamd, stubber, reports):
    """clamd answering OK before the end of the stream has not seen the whole file."""
    fake_clamd.close_after = 2 * MiB
    fake_clamd.reply = b'stream: OK\0'
    data = os.urandom(12 * MiB)
    obj = stored(size=len(data))
    expect_get(stubber, obj, data)

    assert tasks.scan_object_task.call(str(obj.pk)) == 'retry'
    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.QUARANTINE


@pytest.mark.django_db
def test_clamd_down_keeps_the_object_in_quarantine_and_retries(stubber, settings, tmp_path,
                                                                caplog, reports):
    settings.STORAGE_CLAMD_SOCKET = str(tmp_path / 'refused.ctl')
    obj = stored()
    expect_get(stubber, obj, b'abc')

    assert tasks.scan_object_task.call(str(obj.pk)) == 'retry'

    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.QUARANTINE
    assert not DeletionTrace.objects.exists()
    retry = DBTaskResult.objects.get()
    assert (retry.backend_name, retry.queue_name) == ('scans', 'scans')
    assert retry.args_kwargs == {'args': [str(obj.pk), 2], 'kwargs': {}}
    assert retry.run_after is not None
    assert 'clamd_unavailable' in caplog.text
    assert_nothing_sensitive(caplog.text, obj)
    assert reports == []   # Sentry only after the last attempt


@pytest.mark.django_db
def test_clamd_silent_is_retried_not_decided(fake_clamd, stubber, reports):
    fake_clamd.reply = None
    obj = stored()
    expect_get(stubber, obj, b'abc')
    assert tasks.scan_object_task.call(str(obj.pk), tasks.MAX_ATTEMPTS - 1) == 'retry'
    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.QUARANTINE
    assert reports == []


@pytest.mark.django_db
def test_an_s3_error_is_retried(fake_clamd, stubber, caplog, reports):
    obj = stored()
    stubber.add_client_error('get_object', service_error_code='InternalError',
                             http_status_code=500)
    assert tasks.scan_object_task.call(str(obj.pk)) == 'retry'
    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.QUARANTINE
    assert 's3_error' in caplog.text
    assert reports == []


@pytest.mark.django_db
def test_the_last_failed_attempt_is_reported_by_uuid_and_code(stubber, settings, tmp_path,
                                                              reports):
    settings.STORAGE_CLAMD_SOCKET = str(tmp_path / 'refused.ctl')
    obj = stored()
    expect_get(stubber, obj, b'abc')

    assert tasks.scan_object_task.call(str(obj.pk), tasks.MAX_ATTEMPTS) == 'failed'

    assert not DBTaskResult.objects.exists()
    (report,) = reports
    assert str(obj.pk) in report and 'clamd_unavailable' in report
    assert_nothing_sensitive(report, obj)
    assert settings.STORAGE_CLAMD_SOCKET not in report


@pytest.mark.django_db
def test_interrupted_after_the_copy_ends_clean_on_the_rerun(fake_clamd, stubber, monkeypatch):
    """Acceptance: a scan interrupted after the copy to clean/ but before the state update
    ends `clean` on its next run, with no second copy and no error."""
    obj = stored()
    expect_get(stubber, obj, b'abc')
    clean = {'Bucket': BUCKET, 'Key': f'clean/{obj.pk}'}
    stubber.add_client_error('head_object', service_error_code='404', http_status_code=404,
                             expected_params=clean)
    stubber.add_response('copy_object', {}, {
        **clean, 'CopySource': {'Bucket': BUCKET, 'Key': f'quarantine/{obj.pk}'}})
    real_delete = s3.delete_keys

    def killed(*args, **kwargs):
        raise SystemExit('worker killed')

    monkeypatch.setattr(s3, 'delete_keys', killed)
    with pytest.raises(SystemExit):
        tasks.scan_object_task.call(str(obj.pk))
    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.QUARANTINE

    # The rerun: the quarantine object is still there and scans clean again; the clean
    # copy exists, so it is not copied again.
    monkeypatch.setattr(s3, 'delete_keys', real_delete)
    expect_get(stubber, obj, b'abc')
    expect_promotion(stubber, obj, clean_exists=True)

    assert tasks.scan_object_task.call(str(obj.pk)) == 'clean'
    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.CLEAN


@pytest.mark.django_db
def test_rerun_after_the_quarantine_delete_finishes_without_rescanning(fake_clamd, stubber):
    obj = stored()
    stubber.add_client_error('get_object', service_error_code='NoSuchKey',
                             http_status_code=404,
                             expected_params={'Bucket': BUCKET, 'Key': f'quarantine/{obj.pk}'})
    stubber.add_response('head_object', {'ContentLength': 3},
                         {'Bucket': BUCKET, 'Key': f'clean/{obj.pk}'})
    # No second HEAD or copy: only the (already gone) quarantine object is deleted.
    stubber.add_response('delete_object', {}, {'Bucket': BUCKET, 'Key': f'quarantine/{obj.pk}'})

    assert tasks.scan_object_task.call(str(obj.pk)) == 'clean'
    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.CLEAN
    assert fake_clamd.data == []


def expect_missing(stubber, obj):
    stubber.add_client_error('get_object', service_error_code='NoSuchKey', http_status_code=404,
                             expected_params={'Bucket': BUCKET, 'Key': f'quarantine/{obj.pk}'})
    stubber.add_client_error('head_object', service_error_code='404', http_status_code=404,
                             expected_params={'Bucket': BUCKET, 'Key': f'clean/{obj.pk}'})


@pytest.mark.django_db
def test_an_object_under_neither_key_is_rejected_as_missing(fake_clamd, stubber, reports,
                                                            caplog):
    obj = stored()
    expect_missing(stubber, obj)
    expect_rejection(stubber, obj)   # deleting keys already gone is no error in S3

    assert tasks.scan_object_task.call(str(obj.pk)) == 'missing'

    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.REJECTED
    trace = DeletionTrace.objects.get()
    assert (trace.object_id, trace.actor_type) == (obj.pk, 'system')
    assert fake_clamd.received == [] and reports == []
    assert 'missing' in caplog.text
    assert_nothing_sensitive(caplog.text, obj)


@pytest.mark.django_db
def test_an_interrupted_rejection_ends_rejected_on_the_rerun(fake_clamd, stubber, monkeypatch,
                                                             reports):
    """A worker killed after the rejection deleted the keys, before the state was saved:
    the rerun finds neither key and finishes the rejection, instead of retrying (and
    reporting to Sentry) for ever."""
    fake_clamd.reply = b'stream: Win.Test.EICAR_HDB-1 FOUND\0'
    obj = stored()
    expect_get(stubber, obj, EICAR)
    expect_rejection(stubber, obj)
    real_delete = s3.delete_keys

    def deleted_then_killed(*args, **kwargs):
        real_delete(*args, **kwargs)
        raise SystemExit('worker killed')

    monkeypatch.setattr(s3, 'delete_keys', deleted_then_killed)
    with pytest.raises(SystemExit):
        tasks.scan_object_task.call(str(obj.pk))
    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.QUARANTINE
    assert not DeletionTrace.objects.exists()

    monkeypatch.setattr(s3, 'delete_keys', real_delete)
    expect_missing(stubber, obj)
    expect_rejection(stubber, obj)

    assert tasks.scan_object_task.call(str(obj.pk)) == 'missing'
    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.REJECTED
    assert DeletionTrace.objects.filter(object_id=obj.pk, actor_type='system').count() == 1
    assert reports == []


@pytest.mark.django_db
def test_scanned_deleted_or_deleting_objects_are_left_alone(fake_clamd, stubber):
    clean = stored(state=StoredObject.CLEAN)
    rejected = stored(state=StoredObject.REJECTED)
    deleting = stored(delete_requested_at=timezone.now())
    assert tasks.scan_object_task.call(str(clean.pk)) == 'already scanned'
    assert tasks.scan_object_task.call(str(rejected.pk)) == 'already scanned'
    assert tasks.scan_object_task.call(str(deleting.pk)) == 'skipped'
    assert tasks.scan_object_task.call(str(uuid.uuid4())) == 'skipped'
    assert fake_clamd.received == []


def expect_copy(stubber, obj):
    clean = {'Bucket': BUCKET, 'Key': f'clean/{obj.pk}'}
    stubber.add_client_error('head_object', service_error_code='404', http_status_code=404,
                             expected_params=clean)
    stubber.add_response('copy_object', {}, {
        **clean, 'CopySource': {'Bucket': BUCKET, 'Key': f'quarantine/{obj.pk}'}})


@pytest.mark.django_db
def test_deletion_asked_during_the_scan_wins(stubber, monkeypatch):
    obj = stored()
    expect_get(stubber, obj, b'abc')
    expect_copy(stubber, obj)
    # The deletion wins: the clean copy just made is deleted too.
    stubber.add_response('delete_object', {}, {'Bucket': BUCKET, 'Key': f'clean/{obj.pk}'})

    def scan_stream(chunks, **kwargs):
        list(chunks)
        StoredObject.objects.filter(pk=obj.pk).update(delete_requested_at=timezone.now())
        return clamd.Verdict(clamd.OK)

    monkeypatch.setattr(tasks.clamd, 'scan_stream', scan_stream)
    assert tasks.scan_object_task.call(str(obj.pk)) == 'skipped'
    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.QUARANTINE


@pytest.mark.django_db
@pytest.mark.parametrize('how', ['deletion asked', 'row deleted'])
def test_deletion_during_the_copy_deletes_the_clean_copy(fake_clamd, stubber, monkeypatch, how):
    """The copy runs before the row lock; a deletion that came meanwhile (its task may
    have run already) wins: the new clean object is deleted, the row is not promoted."""
    obj = stored()
    expect_get(stubber, obj, b'abc')
    expect_copy(stubber, obj)
    stubber.add_response('delete_object', {}, {'Bucket': BUCKET, 'Key': f'clean/{obj.pk}'})
    real_copy = s3.copy_object

    def copy_while_deleted(*args, **kwargs):
        real_copy(*args, **kwargs)
        rows = StoredObject.objects.filter(pk=obj.pk)
        if how == 'row deleted':
            rows.delete()
        else:
            rows.update(delete_requested_at=timezone.now())

    monkeypatch.setattr(s3, 'copy_object', copy_while_deleted)
    assert tasks.scan_object_task.call(str(obj.pk)) == 'skipped'
    assert not StoredObject.objects.filter(pk=obj.pk, state=StoredObject.CLEAN).exists()


def run_scan_worker():
    call_command('db_worker', '--batch', '--no-startup-delay', '--no-reload',
                 '--backend', 'scans', '--queue-name', 'scans', verbosity=0,
                 stdout=io.StringIO())


@pytest.mark.django_db(transaction=True)
def test_the_task_twice_gives_one_outcome_and_no_error(fake_clamd, stubber):
    """A deploy restart may run a scan twice: one outcome, no error."""
    obj = stored()
    expect_get(stubber, obj, b'abc')
    expect_promotion(stubber, obj)
    tasks.scan_object_task.enqueue(str(obj.pk))
    tasks.scan_object_task.enqueue(str(obj.pk))

    run_scan_worker()

    results = DBTaskResult.objects.all()
    assert sorted(r.return_value for r in results) == ['already scanned', 'clean']
    assert all(r.status == 'SUCCESSFUL' and r.backend_name == 'scans' for r in results)
    assert StoredObject.objects.get(pk=obj.pk).state == StoredObject.CLEAN
    assert len(fake_clamd.data) == 1


@pytest.mark.django_db(transaction=True)
def test_the_main_worker_never_runs_a_scan(fake_clamd):
    obj = stored()
    tasks.scan_object_task.enqueue(str(obj.pk))
    call_command('db_worker', '--batch', '--no-startup-delay', '--no-reload',
                 '--queue-name', '*', verbosity=0, stdout=io.StringIO())
    assert DBTaskResult.objects.get().status == 'READY'


@pytest.mark.django_db(transaction=True)
def test_requeue_of_one_worker_leaves_the_others_tasks(fake_clamd):
    from django.tasks import TaskResultStatus
    obj = stored()
    tasks.scan_object_task.enqueue(str(obj.pk))
    tasks.delete_object_task.enqueue(str(obj.pk))
    DBTaskResult.objects.update(status=TaskResultStatus.RUNNING)

    call_command('requeue_interrupted_tasks', '--backend', 'scans', stdout=io.StringIO())

    statuses = dict(DBTaskResult.objects.values_list('backend_name', 'status'))
    assert statuses == {'scans': 'READY', 'default': 'RUNNING'}


# --- Download before the scan ---------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('purpose', [TEST_PURPOSE, 'sample'])
@pytest.mark.parametrize('state', [StoredObject.QUARANTINE, StoredObject.REJECTED])
def test_nothing_unscanned_is_downloadable(staff, doctor, monkeypatch, purpose, state):
    user = doctor if purpose == TEST_PURPOSE else staff
    obj = stored(purpose=purpose, uploaded_by=user, state=state)
    monkeypatch.setattr('storage.s3.presign_download', lambda *a, **k: pytest.fail('no URL'))
    with pytest.raises(service.DownloadRefused):
        service.download_url(obj, user, 'test_reason')


# --- Sweep -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_sweep_enqueues_unfinished_scans_again(stubber):
    stubber.add_response('list_objects_v2', {}, {'Bucket': BUCKET, 'Prefix': 'quarantine/'})
    hour_ago = timezone.now() - timedelta(hours=1, minutes=1)
    stuck = stored(created_at=hour_ago)
    stored(created_at=timezone.now() - timedelta(minutes=30))          # still in its retries
    stored(created_at=hour_ago, state=StoredObject.CLEAN)
    stored(created_at=hour_ago, state=StoredObject.REJECTED)
    stored(created_at=hour_ago, delete_requested_at=timezone.now())     # being deleted

    out = io.StringIO()
    call_command('sweep_storage', stdout=out)

    assert '1 scans enqueued again' in out.getvalue()
    assert str(stuck.pk) not in out.getvalue()
    (queued,) = DBTaskResult.objects.filter(backend_name='scans')
    assert queued.task_path == 'storage.tasks.scan_object_task'
    assert queued.args_kwargs == {'args': [str(stuck.pk)], 'kwargs': {}}


@pytest.mark.django_db
def test_sweep_does_not_double_a_scan_already_waiting_or_running(stubber):
    from django.tasks import TaskResultStatus
    stubber.add_response('list_objects_v2', {}, {'Bucket': BUCKET, 'Prefix': 'quarantine/'})
    hour_ago = timezone.now() - timedelta(hours=1, minutes=1)
    waiting, running, done, lost = (stored(created_at=hour_ago) for _ in range(4))
    tasks.scan_object_task.enqueue(str(waiting.pk))
    running_task = tasks.scan_object_task.using(
        run_after=timezone.now() + timedelta(minutes=8)).enqueue(str(running.pk), 4)
    done_task = tasks.scan_object_task.enqueue(str(done.pk))
    DBTaskResult.objects.filter(id=running_task.id).update(status=TaskResultStatus.RUNNING)
    DBTaskResult.objects.filter(id=done_task.id).update(status=TaskResultStatus.SUCCESSFUL)
    # A deletion task naming `lost` on the default backend does not count.
    tasks.delete_object_task.enqueue(str(lost.pk))

    out = io.StringIO()
    call_command('sweep_storage', stdout=out)

    assert '2 scans enqueued again' in out.getvalue()
    scans = [r.args_kwargs['args'] for r in DBTaskResult.objects.filter(
        backend_name='scans', status=TaskResultStatus.READY)]
    assert sorted(scans, key=str) == sorted(
        [[str(waiting.pk)], [str(done.pk)], [str(lost.pk)]], key=str)


@pytest.mark.django_db
def test_sweep_reports_scans_waiting_over_6_hours_once_by_count(stubber, monkeypatch):
    import sentry_sdk
    sent = []
    monkeypatch.setattr(sentry_sdk, 'capture_message',
                        lambda message, level=None: sent.append((message, level)))
    stubber.add_response('list_objects_v2', {}, {'Bucket': BUCKET, 'Prefix': 'quarantine/'})
    old = timezone.now() - timedelta(hours=6, minutes=1)
    stale = [stored(created_at=old) for _ in range(2)]
    stored(created_at=timezone.now() - timedelta(hours=5))
    stored(created_at=old, state=StoredObject.CLEAN)
    stored(created_at=old, delete_requested_at=timezone.now())

    out = io.StringIO()
    call_command('sweep_storage', stdout=out)

    assert '2 waiting for a scan over 6 hours' in out.getvalue()
    ((message, level),) = sent
    assert level == 'error' and message.startswith('2 stored object(s) waiting')
    for obj in stale:
        assert str(obj.pk) not in message
    assert NAME not in message and 'quarantine/' not in message


@pytest.mark.django_db
def test_sweep_sends_nothing_when_no_scan_is_late(stubber, monkeypatch):
    import sentry_sdk
    monkeypatch.setattr(sentry_sdk, 'capture_message', lambda *a, **k: pytest.fail('report'))
    stubber.add_response('list_objects_v2', {}, {'Bucket': BUCKET, 'Prefix': 'quarantine/'})
    stored(created_at=timezone.now() - timedelta(hours=2))
    out = io.StringIO()
    call_command('sweep_storage', stdout=out)
    assert '0 waiting for a scan over 6 hours' in out.getvalue()


# --- Settings, check and clamd_check -------------------------------------------------------

def test_storage_without_a_clamd_socket_is_an_error(settings):
    settings.STORAGE_CLAMD_SOCKET = ''
    (error,) = checks.check_storage_settings()
    assert error.id == 'storage.E004' and 'STORAGE_CLAMD_SOCKET' in error.msg
    settings.STORAGE_SAMPLE_UPLOADS = False
    settings.STORAGE_BUCKET = ''
    assert checks.check_storage_settings() == []


def test_the_scans_backend_is_its_own():
    from django.conf import settings
    assert settings.TASKS['scans'] == {'BACKEND': 'django_tasks_db.DatabaseBackend',
                                       'QUEUES': ['scans']}
    assert (tasks.scan_object_task.backend, tasks.scan_object_task.queue_name) == (
        'scans', 'scans')
    assert tasks.delete_object_task.backend == 'default'


def test_clamd_check_prints_the_version(fake_clamd):
    signed = timezone.now().replace(tzinfo=None) - timedelta(hours=5)
    fake_clamd.reply = f'ClamAV 1.4.3/27450/{signed:%a %b %d %H:%M:%S %Y}\0'.encode()
    out = io.StringIO()
    call_command('clamd_check', stdout=out)
    assert 'clamd: ClamAV 1.4.3/27450/' in out.getvalue()
    assert 'signatures: current' in out.getvalue()


def test_clamd_check_fails_on_old_signatures_or_no_clamd(fake_clamd, settings, tmp_path):
    fake_clamd.reply = b'ClamAV 1.4.3/27000/Mon Jan  5 08:30:00 2026\0'
    with pytest.raises(CommandError, match='days old'):
        call_command('clamd_check', stdout=io.StringIO())
    settings.STORAGE_CLAMD_SOCKET = str(tmp_path / 'none.ctl')
    with pytest.raises(CommandError, match='not reachable'):
        call_command('clamd_check', stdout=io.StringIO())
