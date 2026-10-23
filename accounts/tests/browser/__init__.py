"""The login end-to-end suite (epic-login entry 12): Chromium (Playwright) against
live_server, with the fake verification provider and the test mail outbox, marker
`browser`. Skipped where Playwright or its Chromium is missing (signup/tests/browser/
conftest.py); CI installs both and runs it in its `pytest` step, with DEBUG off and
PostgreSQL 17. The shared steps are helpers.py's.

The spec's success signal: test_success.py::test_success_signal (English and Spanish);
its records: test_login_policies.py::test_records_after_the_journey.

The spec's capabilities:

CAP-1 Log in with email and password, land on welcome --
      test_success.py::test_success_signal, test_login.py::test_signup_user_logs_out_and_logs_in,
      test_login.py::test_wrong_password_shows_the_generic_error
CAP-2 Password reset through the emailed link -- test_success.py::test_success_signal
      (from signup's recovery link), test_password_reset.py::
      test_reset_the_password_and_log_in_with_it (the link works once),
      test_login_policies.py::test_forgot_password_link
CAP-3 Login records (time, IP, browser) --
      test_login_policies.py::test_records_after_the_journey
CAP-4 Page records -- test_login_policies.py::test_records_after_the_journey
CAP-5 Abuse protection (CAPTCHA, the lockout) --
      test_login_policies.py::test_lockout_after_five_wrong_passwords,
      test_login.py::test_signup_user_logs_out_and_logs_in (the CAPTCHA holds the submit)
CAP-6 Log out -- test_success.py::test_success_signal,
      test_login.py::test_signup_user_logs_out_and_logs_in
CAP-7 The second factor (email or mobile, WhatsApp else SMS) --
      test_success.py::test_success_signal (email),
      test_login_policies.py::test_mobile_code_by_sms_without_whatsapp,
      test_login_policies.py::test_wrong_code_then_the_right_one
CAP-8 Remember this device for 90 days -- test_success.py::test_success_signal,
      test_login.py::test_remembered_device_logs_in_without_a_code
CAP-9 One session, the idle limit --
      test_login_policies.py::test_single_session_across_two_browsers,
      test_login_policies.py::test_idle_session_ends
"""
