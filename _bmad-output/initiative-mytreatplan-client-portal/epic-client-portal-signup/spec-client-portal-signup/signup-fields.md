# Signup Fields

All fields are mandatory unless marked optional.

## Step 1: personal, contact and login data (CAP-1)

| Field | Type | Values / rules |
|---|---|---|
| Email | email | First field, checked as soon as it is left. Username. Not a disposable domain. Unique across registered users (CAP-2) and pending registrations: a match offers to resume with a code to the email, which also verifies it (CAP-3) |
| Title / Treatment | select | Honorific: Dr., Prof., Mr, Mrs, Ms, Eng. |
| Name | text | |
| Middle name or initial | text | **Optional** |
| Surname(s) | text | One or more surnames |
| Country / Region | select | |
| Password | password | Min 12 chars; no composition rules; rejected if in a breached-password list (check skipped when that service is unavailable) or too similar to name/email; strength meter shown |
| Repeat password | password | Must equal Password |
| International prefix | select | |
| Mobile phone number | tel | Unique across registered users (CAP-2). A mobile in another pending registration is allowed; that registration is removed when an account is created (CAP-3) |
| How did you find out about us? | select | Search Engine (e.g. Google); Social Media (Instagram, Facebook); LinkedIn; WhatsApp; e-mail; Telephone Call; Conference or Congress; Others |
| Others: please specify | text | Shown and mandatory only when "Others" is selected; hidden otherwise, also without JavaScript |
| CAPTCHA | Turnstile, managed | May show a widget; a challenge only when the risk check flags the attempt. Lets the request through when the service is unavailable |

## Step 2: legal acceptance (CAP-4)

| Document | Rule |
|---|---|
| Terms | Must be accepted; step 2 refuses while no version is published |
| Conditions | Must be accepted; step 2 refuses while no version is published |
| Ormco TPS contract | Always shown, **unticked**, optional (for current or prospective Ormco users) |
| Commercial-use consent (CAP-13) | Always shown, **unticked**, optional. Covers MyTPDSO Limited (Ireland), MyTPDSO Spain SL (Spain) and MyTPDSO LLC-FZ (UAE), by email, WhatsApp, SMS and phone; never shared outside the group |

## Steps 3 and 4: email code and mobile code pages (CAP-5, CAP-6)

| Field | Rules |
|---|---|
| Email code | See `otp-verification.md` |
| Mobile code | See `otp-verification.md`; differs from email code |

## After the mobile code (CAP-7)

No input and not a wizard page. Account created, auto-login, welcome/onboarding screen.
