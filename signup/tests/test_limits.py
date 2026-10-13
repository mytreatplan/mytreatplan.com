"""Our resend limits (signup.limits): a minute between sends, 4 sends per hour (the
first plus 3 resends), per contact and purpose, in the database cache."""
import pytest
from django.conf import settings
from django.db import connection

from accounts.crypto import email_index, phone_index
from signup import limits

pytestmark = pytest.mark.django_db

EMAIL = 'ana@example.com'
PHONE = '+34622918374'


def test_storage_is_the_database_cache():
    assert settings.CACHES['default'] == {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'django_cache',
        'OPTIONS': {'MAX_ENTRIES': 100000},
    }


def test_first_send_is_allowed(clock):
    assert limits.check_send(EMAIL, 'signup') == (True, 0, False)
    assert limits.seconds_since_last_send(EMAIL, 'signup') is None


def test_a_minute_between_sends(clock):
    limits.record_send(PHONE, 'signup')
    clock.advance(1)
    assert limits.check_send(PHONE, 'signup') == (False, 59, False)
    clock.advance(58.5)
    assert limits.check_send(PHONE, 'signup') == (False, 1, False)
    clock.advance(0.5)
    assert limits.check_send(PHONE, 'signup').allowed
    assert limits.seconds_since_last_send(PHONE, 'signup') == 60


def test_four_sends_per_hour(clock):
    for _send in range(4):
        assert limits.check_send(EMAIL, 'signup').allowed
        limits.record_send(EMAIL, 'signup')
        clock.advance(60)
    # 240 s after the first send: the next one is allowed 3360 s from now.
    assert limits.check_send(EMAIL, 'signup') == (False, 3360, True)
    clock.advance(3359)
    assert limits.check_send(EMAIL, 'signup').hourly_blocked
    clock.advance(1)
    assert limits.check_send(EMAIL, 'signup').allowed


def test_limits_are_per_contact_and_purpose(clock):
    limits.record_send(EMAIL, 'signup')
    assert not limits.check_send(EMAIL, 'signup').allowed
    assert not limits.check_send('ANA@example.com', 'signup').allowed  # same blind index
    assert limits.check_send('bea@example.com', 'signup').allowed
    assert limits.check_send(EMAIL, 'resume').allowed
    assert limits.check_send(PHONE, 'signup').allowed


def test_keys_hold_the_blind_index_never_the_contact(clock):
    limits.record_send(EMAIL, 'signup')
    limits.record_send(PHONE, 'signup')
    with connection.cursor() as cursor:
        cursor.execute('SELECT cache_key, value FROM django_cache')
        rows = cursor.fetchall()
    keys = ' '.join(key for key, _value in rows)
    assert email_index(EMAIL) in keys and phone_index(PHONE) in keys
    for key, value in rows:
        for contact in (EMAIL, PHONE):
            assert contact not in key and contact not in value
