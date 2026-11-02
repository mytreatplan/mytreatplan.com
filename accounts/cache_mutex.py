"""A short-lived lock in CACHES['default'] (the database cache), around a read-then-write
of a counter there: a re-export of `mytp_core.cache_mutex` (epic-admin-access entry 3),
where the code moved unchanged in behaviour.

Kept because the login failure counters (accounts.login_failures), the password reset's
per-email limit (accounts.password_reset), the per-IP limits (signup.limits) and the
contact form (mytp_publicsite.contact) import it from here.
"""
from mytp_core.cache_mutex import CounterBusy, acquire, cache_mutex  # noqa: F401
