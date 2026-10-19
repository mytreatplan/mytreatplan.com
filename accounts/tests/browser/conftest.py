"""The login browser tests reuse the signup browser tests' setup (signup/tests/browser/
conftest.py): plain HTTP for live_server, published legal documents and the Turnstile stub,
and signup's empty cache at the start of each test (signup/tests/conftest.py).
That conftest's collection hook already skips every `browser` test where Chromium is
missing."""
from signup.tests.browser.conftest import legal_documents, plain_http, turnstile_script  # noqa: F401
from signup.tests.conftest import clear_cache  # noqa: F401
