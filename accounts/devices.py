"""The remembered device (epic-login entry 3, CAP-8): "Remember me on this device/browser
for 90 days" on the login code page.

The mark is a signed cookie only (decision 1: no table): `django.core.signing` with its own
salt, carrying the account id, the issue time and the account's session auth hash. It is
valid while the signature checks, the issue time is less than REMEMBER_SECONDS old (fixed
from the moment it was given: a later login does not extend it) and the hash still matches
the account, so a password change or reset forgets every remembered device of the account.
Each account has its own cookie, named from a keyed, non-reversible digest of its id, so
several accounts in one browser keep their own marks. The cookie holds no email or phone;
nothing here logs.

Checked only by the login view, after a right password and a passed CAPTCHA, for the
account that just passed them. Logout and the ends of a session leave it in place.
"""
from django.conf import settings
from django.core import signing
from django.utils.crypto import constant_time_compare, salted_hmac

REMEMBER_DAYS = 90
REMEMBER_SECONDS = REMEMBER_DAYS * 24 * 60 * 60

SIGNING_SALT = 'accounts.devices.remembered'
NAME_SALT = 'accounts.devices.cookie-name'
COOKIE_PREFIX = 'mtp_device_'


def cookie_name(user):
    """This account's cookie: a keyed digest of its id (not reversible to the id)."""
    digest = salted_hmac(NAME_SALT, str(user.pk), algorithm='sha256').hexdigest()
    return COOKIE_PREFIX + digest[:24]


def issue(user, now):
    """The signed mark for `user`, given at `now` (epoch seconds)."""
    return signing.dumps({'u': str(user.pk), 't': int(now), 'h': user.get_session_auth_hash()},
                         salt=SIGNING_SALT)


def is_valid(value, user, now):
    """Whether `value` is a valid mark of `user` at `now`: the signature checks, it was
    given for this account less than REMEMBER_SECONDS ago, and the account's session
    auth hash (its password) is the same."""
    if not value:
        return False
    try:
        data = signing.loads(value, salt=SIGNING_SALT)
    except signing.BadSignature:
        return False
    if not isinstance(data, dict):
        return False
    issued = data.get('t')
    if not isinstance(issued, int) or not 0 <= now - issued < REMEMBER_SECONDS:
        return False
    return (constant_time_compare(str(data.get('u', '')), str(user.pk))
            and constant_time_compare(str(data.get('h', '')), user.get_session_auth_hash()))


def is_remembered(request, user, now):
    """Whether this browser holds a valid mark of `user`."""
    return is_valid(request.COOKIES.get(cookie_name(user)), user, now)


def remember(response, user, now):
    """Give this browser `user`'s mark on `response`: 90 days from `now`, HttpOnly,
    SameSite=Lax, Secure as the session cookie is (when DEBUG is off)."""
    response.set_cookie(
        cookie_name(user), issue(user, now),
        max_age=REMEMBER_SECONDS,
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite='Lax',
    )
    return response
