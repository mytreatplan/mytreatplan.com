---
id: SPEC-client-portal-signup
companions:
  - signup-fields.md
  - otp-verification.md
  - registration-failure-policies.md
  - encryption-policy.md
  - brownfield.md
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# Client Portal Signup

## Why

Vision plus mandate. mytreatplan.com is the client portal where Doctors order Mytreatplan's digital orthodontic treatment-planning services (clear aligners of any brand, in-house aligners, direct-bonded brackets), plus mentoring, clinical support, Dental Monitoring supervision and education. The current public landing (mytreatplan.ae) has no signup, so no Doctor can become a client online. Signup is the first portal feature after the landing is integrated, and every later feature (login, billing, orders, patient uploads, support) depends on a verified, securely stored Doctor account. Handling personal data of EU-based operations puts it under GDPR.

## Capabilities

- **CAP-1**
  - **intent:** A Doctor enters personal, contact and login data in wizard step 1, email first (fields in `signup-fields.md`).
  - **success:** Step 1 cannot be left with a required field empty or invalid, mismatched passwords, or a password failing the policy. The email is checked (disposable, registered, pending) as soon as the field is left. Valid data is saved server-side as a pending registration.
- **CAP-2**
  - **intent:** The system rejects an email or mobile number already belonging to a registered user.
  - **success:** Entering a registered email or mobile blocks progress and shows a message linking to password recovery. The check runs again at account creation.
- **CAP-3**
  - **intent:** A Doctor whose email is in a pending registration can resume it after proving control of that email.
  - **success:** A warning offers to resume. A correct code sent to that email restores the data entered so far into step 1 for review, verifies the email, and the new attempt replaces the old pending registration, keeping its original start time. Without the code nothing is revealed. A mobile in another pending registration is allowed; that registration is removed when an account is created.
- **CAP-4**
  - **intent:** In step 2 the Doctor reviews and accepts the Terms and the Conditions, and may accept the Ormco TPS contract.
  - **success:** Step 2 cannot be completed without accepting Terms and Conditions. While either has no published version, step 2 refuses with "Signup is not available right now", and no account is created without both accepted. The Ormco TPS contract is always shown, unticked, and optional. Each acceptance is stored with the registration. Once the current versions are accepted, step 2 can be passed again without re-ticking; a version published after step 2 was shown is asked for again.
- **CAP-5**
  - **intent:** The Doctor proves control of their email with a one-time code (`otp-verification.md`).
  - **success:** A correct code marks the email verified; a wrong, expired or exhausted code does not.
- **CAP-6**
  - **intent:** The Doctor proves control of their mobile with a one-time code, sent by WhatsApp first and by SMS as fallback (`otp-verification.md`).
  - **success:** The code goes by WhatsApp when it is enabled. If delivery fails or the user asks, it goes by SMS. It differs from the email code, and correct entry marks the mobile verified.
- **CAP-7**
  - **intent:** Once both contacts are verified, the account is created and the Doctor is logged in automatically to the welcome/onboarding screen.
  - **success:** After both verifications, the user exists and lands logged in on the welcome/onboarding screen with no extra step. Later logins use email, password and the login spec's second factor. A completed signup removes other pending registrations with the same email.
- **CAP-8**
  - **intent:** The Doctor can cancel registration from any wizard step.
  - **success:** "Cancel registration" asks for confirmation. On confirm, the registration store is cleared at once and the form resets; the attempt's log keeps the reason and field name, and its personal values are anonymized 30 days after the attempt ends (CAP-10).
- **CAP-9**
  - **intent:** Unfinished registrations are cleared automatically.
  - **success:** A registration not completed within 24h (cleared by an hourly job, so within about 25h) has no personal data left in the registration store, and its attempt ends with the reason "timeout". Its log is anonymized 30 days after the attempt ends, as CAP-10: it then keeps only the failure reason and field type/name (e.g. "phone already exists", "OTP failed", "timeout").
- **CAP-10**
  - **intent:** Every piece of data received or captured during signup is logged for debugging and for detecting fraud and attacks, except passwords, codes and the CAPTCHA token.
  - **success:** Any signup attempt can be reconstructed step by step from the log, each event with its IP and User-Agent. Personal data in log entries is anonymized 30 days after the attempt ends, and anonymized entries are deleted after 2 years.
- **CAP-11**
  - **intent:** Each signup failure scenario has a defined behavior (`registration-failure-policies.md`).
  - **success:** Every scenario in the companion has a test showing the system follows its policy.
- **CAP-13**
  - **intent:** In step 2 the Doctor may consent to the Mytreatplan group companies (MyTPDSO Limited, Ireland; MyTPDSO Spain SL, Spain; MyTPDSO LLC-FZ, UAE) contacting them for commercial purposes by email, WhatsApp, SMS and phone. The data is never shared outside the group.
  - **success:** The consent box is shown unticked, and signup completes whether or not it is ticked. The choice is stored with timestamp and the version of the consent text.

## Constraints

- Only Doctors (individuals or company representatives) sign up; patients never do. No company data is collected at signup.
- Email is the username.
- Every field is mandatory except middle name/initial.
- The wizard has 4 pages: account (step 1), legal (step 2), email code, mobile code. Welcome follows account creation and is not a wizard page.
- The wizard follows the MyTreatPlan design system (https://claude.ai/artifact/N6SvdLiD4asJTiZJakaqB3).
- UI, emails and code messages are in English and Spanish.
- No user account exists until both email and mobile are verified.
- Wizard progress is saved server-side at each step; uniqueness checks (CAP-2, CAP-3) run before leaving step 1 and again at account creation.
- Each legal acceptance and consent choice is stored with timestamp and document version as evidence.
- Personal data is encrypted at rest per `encryption-policy.md`. Passwords are hashed with Argon2id, never encrypted.
- Signup logs and personal data are handled per EU GDPR. Commercial consent is never required for signup, never pre-ticked, and its text names every group company that receives the data.
- Final legal and consent texts, including the safeguard for transfers to MyTPDSO LLC-FZ (UAE), come from the company lawyer after development starts. Texts are versioned (CAP-4, CAP-13), so a new version replaces the old one.
- No personal data from a cancelled or expired registration remains in the registration store: it is cleared at once. Failure logs keep the reason and field name; their personal values are anonymized 30 days after the attempt ends (CAP-10).
- Backups keep cancelled or purged registrations and log values not yet anonymized for up to 30 days, then expire (the stated policy; the Privacy text says so).
- Twilio Verify sends and checks all codes (email via SendGrid; mobile via WhatsApp, then SMS), through one Verify service for every purpose. Codes are never stored in our database. Twilio credentials come from environment configuration.
- Anti-abuse: CAPTCHA (Cloudflare Turnstile, managed mode, which may show a widget) on step 1; rate limits per IP (signup starts, email checks, code sends) and per contact (code sends); disposable email domains rejected. The CAPTCHA, breached-password and disposable-email checks let the request through when their service is unavailable.
- Signup needs JavaScript, as the step-1 CAPTCHA does; there is no path without it. Step 1 tells a visitor without JavaScript to turn it on.
- An admin can erase an account: it is deactivated at once and keeps only evidence (email, title, names, country, phone, date joined, legal choices) for a lawyer-set retention period, then is deleted. The email can sign up again at once. Before deletion, one activity record keeps the encrypted email for 5 years.
- Built inside the existing Django/PostgreSQL project (`brownfield.md`).

## Non-goals

- Login page and password recovery page (signup only links to recovery).
- Logging of logins and browsing in the registered area: moved to the login spec (former CAP-12).
- Content of the welcome/onboarding screen: its own spec.
- Integrating the mytreatplan.ae landing into the project (a prior step).
- Contact form.
- Withdrawing commercial consent after signup (belongs to profile settings; GDPR requires it to exist).
- Billing and company data, profile settings beyond step 1, knowledge base, orders, patient data upload, invoices, support.
- Patient accounts.
- Tracking whether a code was delivered (no provider webhooks).

## Success signal

- A new Doctor completes the four wizard pages, in English or Spanish, verifies email and mobile, and lands logged in on the welcome screen. A second attempt with the same email or mobile is blocked with a recovery link. An attempt abandoned for 24h leaves no personal data in the registration store; its log is anonymized 30 days after the attempt ends, as CAP-10.
