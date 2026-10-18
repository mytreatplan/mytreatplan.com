"""The signup end-to-end suite (signup entry 14): Chromium (Playwright) against
live_server, with the fake verification provider, marker `browser`. Skipped where
Playwright or its Chromium is missing (conftest.py); CI installs both and runs it in its
`pytest` step, with DEBUG off, compiled messages, the built CSS and PostgreSQL 17.

The spec's Success signal: test_success.py::test_success_signal (English and Spanish).

registration-failure-policies.md, row by row:

 1. User cancels (Cancel Registration) -- test_failure_policies.py::test_row1_user_cancels
 2. Registration unfinished after 24h -- test_failure_policies.py::test_row2_unfinished_after_24h
 3. Email already registered -- test_failure_policies.py::test_row3_email_already_registered
 4. Mobile already registered -- test_failure_policies.py::test_row4_mobile_already_registered
 5. Email in a pending registration --
    test_failure_policies.py::test_row5_email_in_a_pending_registration
 6. User closes the page mid-wizard -- test_failure_policies.py::test_row6_page_closed_mid_wizard
 7. Idle for 30 minutes -- test_failure_policies.py::test_row7_idle_for_30_minutes
 8. Code not received -- test_failure_policies.py::test_row8_code_not_received
 9. Wrong code entered -- test_failure_policies.py::test_row9_wrong_code_entered
10. Resend limit reached -- test_failure_policies.py::test_row10_resend_limit_reached
11. User loses connectivity -- test_connection.py::test_connection_lost_then_back
12. Twilio or our server fails while sending a code --
    test_failure_policies.py::test_row12_send_fails
13. CAPTCHA or rate limit blocks the attempt --
    test_failure_policies.py::test_row13_captcha_or_rate_limit_blocks
14. Disposable email domain -- test_failure_policies.py::test_row14_disposable_email_domain
"""
