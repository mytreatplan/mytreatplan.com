"""The SMS fallback (epic-case-lifecycle 10.3): one test per row of the plan's matrix. The
email task writes the SMS row when the email ends `failed`; the tests run the tasks by hand
with the fake sender, and the Twilio request is checked with the HTTP call patched."""
import io
import logging
import urllib.error
import urllib.parse

import pytest
from django.core import mail
from django.core.checks import run_checks

from accounts.models import Profile
from notifications import sms
from notifications.models import Message
from notifications.tasks import send_message
from notifications.tests.test_case_messages import (STAFF, capture, move, queued,  # noqa: F401
                                                    work_order)
from patients.tests.conftest import *  # noqa: F401,F403

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

MOBILE = '+34699111222'


@pytest.fixture(autouse=True)
def mobile_and_fake(doctor, monkeypatch):
    """The doctor has a mobile; the fake sender's sends are recorded in the returned list."""
    doctor.phone = MOBILE
    doctor.save()
    sent = []
    monkeypatch.setattr(sms.FakeSmsSender, 'send',
                        lambda self, to, body, reference='': sent.append((to, body)))
    return sent


def assert_no_private(text, doctor, patient):
    for private in (doctor.email, MOBILE, 'Ana', 'Pérez'):
        assert private not in text


def failing_email(monkeypatch):
    def raises(self, fail_silently=False):
        raise OSError('mailbox ana@example.com')
    monkeypatch.setattr('django.core.mail.EmailMessage.send', raises)


def start(capture, patient, monkeypatch=None):  # noqa: F811
    if monkeypatch:
        failing_email(monkeypatch)
    move(capture, work_order(patient), 'send_for_review', 1)
    email = Message.objects.get(channel='email')
    capture(lambda: send_message.call(str(email.pk)))
    return email


def test_a_sent_email_leads_to_no_sms(patient, capture, mobile_and_fake):  # noqa: F811
    email = start(capture, patient)
    email.refresh_from_db()
    assert email.state == 'sent'
    assert not Message.objects.filter(channel='sms').exists() and mobile_and_fake == []


def test_a_refused_email_sends_one_sms(doctor, patient, capture, monkeypatch, caplog,
                                       mobile_and_fake):  # noqa: F811
    email = start(capture, patient, monkeypatch)
    email.refresh_from_db()
    assert (email.state, email.reason) == ('failed', 'OSError')
    row = Message.objects.get(channel='sms')
    assert (row.kind, row.recipient, row.work_order_id, row.work_order_version) == (
        email.kind, doctor, email.work_order_id, email.work_order_version)
    assert queued()[-1] == str(row.pk)
    with caplog.at_level(logging.DEBUG):
        assert send_message.call(str(row.pk)) == 'sent'
    row.refresh_from_db()
    assert row.state == 'sent' and row.sent_at
    assert str(row.pk) in caplog.text
    assert_no_private(caplog.text, doctor, patient)
    (to, body), = mobile_and_fake
    assert to == MOBILE and body.startswith('MyTreatPlan:') and patient.code in body
    assert f'/patients/{patient.code}/' in body and 'Ana' not in body and 'Pérez' not in body
    assert not mail.outbox


def test_no_login_email_fails_and_the_sms_goes(doctor, patient, capture, mobile_and_fake):  # noqa: F811
    doctor.email = ''
    doctor.save()
    email = start(capture, patient)
    email.refresh_from_db()
    assert (email.state, email.reason) == ('failed', 'no_address')
    row = Message.objects.get(channel='sms')
    assert send_message.call(str(row.pk)) == 'sent' and len(mobile_and_fake) == 1


def test_no_mobile_fails_with_no_mobile(doctor, patient, capture, monkeypatch, caplog,
                                        mobile_and_fake):  # noqa: F811
    doctor.phone = ''
    doctor.save()
    start(capture, patient, monkeypatch)
    row = Message.objects.get(channel='sms')
    with caplog.at_level(logging.DEBUG):
        assert send_message.call(str(row.pk)) == 'failed'
    row.refresh_from_db()
    assert (row.state, row.reason, row.sent_at) == ('failed', 'no_mobile', None)
    assert str(row.pk) in caplog.text and mobile_and_fake == []
    assert_no_private(caplog.text, doctor, patient)


class FakeResponse:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def twilio_settings(settings):
    settings.SMS_SENDER = sms.TWILIO_SENDER
    settings.TWILIO_ACCOUNT_SID = 'ACtest'
    settings.TWILIO_API_KEY_SID = 'SKtest'
    settings.TWILIO_API_KEY_SECRET = 'secret-value'
    settings.TWILIO_MESSAGING_SERVICE_SID = 'MGtest'


def test_the_twilio_request(settings, monkeypatch):
    twilio_settings(settings)
    seen = []
    monkeypatch.setattr('urllib.request.urlopen',
                        lambda request, timeout: seen.append((request, timeout)) or FakeResponse())
    sms.get_sender().send(MOBILE, 'MyTreatPlan: hello', reference='r')
    (request, timeout), = seen
    assert request.full_url == 'https://api.test.invalid/2010-04-01/Accounts/ACtest/Messages.json'
    assert request.get_method() == 'POST' and timeout == sms.SEND_TIMEOUT
    assert urllib.parse.parse_qs(request.data.decode()) == {
        'To': [MOBILE], 'MessagingServiceSid': ['MGtest'], 'Body': ['MyTreatPlan: hello']}
    import base64
    assert request.get_header('Authorization') == 'Basic ' + base64.b64encode(
        b'SKtest:secret-value').decode()


def test_twilio_refuses(doctor, patient, capture, settings, monkeypatch, caplog):  # noqa: F811
    twilio_settings(settings)

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {},
                                     io.BytesIO(b'{"code": 21608, "message": "to ' + MOBILE.encode() + b'"}'))
    monkeypatch.setattr('urllib.request.urlopen', refuse)
    start(capture, patient, monkeypatch)
    row = Message.objects.get(channel='sms')
    with caplog.at_level(logging.DEBUG):
        assert send_message.call(str(row.pk)) == 'failed'
    row.refresh_from_db()
    assert (row.state, row.reason) == ('failed', 'twilio 21608')
    assert str(row.pk) in caplog.text and '21608' in caplog.text and '403' in caplog.text
    for private in (MOBILE, doctor.email, patient.code, 'secret-value', 'Ana', 'Pérez'):
        assert private not in caplog.text
    assert send_message.call(str(row.pk)) is None


def test_twilio_unreachable(patient, capture, settings, monkeypatch, caplog):  # noqa: F811
    twilio_settings(settings)

    def down(request, timeout):
        raise TimeoutError('timed out')
    monkeypatch.setattr('urllib.request.urlopen', down)
    start(capture, patient, monkeypatch)
    row = Message.objects.get(channel='sms')
    with caplog.at_level(logging.DEBUG):
        assert send_message.call(str(row.pk)) == 'failed'
    row.refresh_from_db()
    assert row.reason == 'TimeoutError' and str(row.pk) in caplog.text
    assert send_message.call(str(row.pk)) is None


def test_the_email_task_rerun_makes_no_second_sms(patient, capture, monkeypatch):  # noqa: F811
    email = start(capture, patient, monkeypatch)
    assert send_message.call(str(email.pk)) is None
    assert Message.objects.filter(channel='sms').count() == 1
    from notifications import service
    service._queue_sms(email)
    assert Message.objects.filter(channel='sms').count() == 1


def test_a_spanish_doctor_gets_a_spanish_sms(doctor, patient, capture, monkeypatch,
                                             mobile_and_fake):  # noqa: F811
    Profile.objects.create(user=doctor, language='es')
    start(capture, patient, monkeypatch)
    send_message.call(str(Message.objects.get(channel='sms').pk))
    (_to, body), = mobile_and_fake
    assert body.startswith('MyTreatPlan: paciente') and patient.code in body


def test_the_check_refuses_the_twilio_sender_with_empty_settings(settings):
    settings.SMS_SENDER = sms.TWILIO_SENDER
    errors = [e for e in run_checks() if e.id == 'notifications.E001']
    assert len(errors) == 1 and 'TWILIO_MESSAGING_SERVICE_SID' in errors[0].msg
    twilio_settings(settings)
    assert not [e for e in run_checks() if e.id == 'notifications.E001']
