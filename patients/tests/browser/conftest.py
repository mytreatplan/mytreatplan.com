"""The patient chooser's browser tests reuse the login browser tests' setup
(accounts/tests/browser/conftest.py)."""
from accounts.tests.browser.conftest import (clear_cache, legal_documents, plain_http,  # noqa: F401
                                             turnstile_script)
