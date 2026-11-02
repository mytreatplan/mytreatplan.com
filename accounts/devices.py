"""The remembered device (epic-login entry 3, CAP-8): "Remember me on this device/browser
for 90 days" on the login code page.

The code lives in `mytp_core.devices` (epic-admin-access entry 3), with the portal's
policy (`mytp_core.policy.PORTAL`: 90 days); this module keeps the portal's names, its
salts and cookie prefix (so the marks already given stay valid) and its module-level
functions, which the login view and the tests use.

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
from mytp_core.devices import RememberedDevices
from mytp_core.policy import PORTAL

POLICY = PORTAL

REMEMBER_DAYS = POLICY.remember_days
REMEMBER_SECONDS = POLICY.remember_seconds

SIGNING_SALT = 'accounts.devices.remembered'
NAME_SALT = 'accounts.devices.cookie-name'
COOKIE_PREFIX = 'mtp_device_'


class PortalDevices(RememberedDevices):
    """The core class with the portal's names; its seams go through this module's
    functions and constants, so what a test patches here applies."""
    cookie_prefix = COOKIE_PREFIX

    @property
    def signing_salt(self):
        return SIGNING_SALT

    @property
    def name_salt(self):
        return NAME_SALT

    def cookie_name(self, user):
        return cookie_name(user)

    def issue(self, user, now):
        return issue(user, now)

    def is_valid(self, value, user, now):
        return is_valid(value, user, now)


_devices = PortalDevices(POLICY)


def cookie_name(user):
    """This account's cookie: a keyed digest of its id (not reversible to the id)."""
    return RememberedDevices.cookie_name(_devices, user)


def issue(user, now):
    """The signed mark for `user`, given at `now` (epoch seconds)."""
    return RememberedDevices.issue(_devices, user, now)


def is_valid(value, user, now):
    """Whether `value` is a valid mark of `user` at `now`: the signature checks, it was
    given for this account less than REMEMBER_SECONDS ago, and the account's session
    auth hash (its password) is the same."""
    return RememberedDevices.is_valid(_devices, value, user, now)


def is_remembered(request, user, now):
    """Whether this browser holds a valid mark of `user`."""
    return _devices.is_remembered(request, user, now)


def remember(response, user, now):
    """Give this browser `user`'s mark on `response`: 90 days from `now`, HttpOnly,
    SameSite=Lax, Secure as the session cookie is (when DEBUG is off)."""
    return _devices.remember(response, user, now)
