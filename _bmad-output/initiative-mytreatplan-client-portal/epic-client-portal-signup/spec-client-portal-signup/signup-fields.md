# Signup Fields

All fields are mandatory unless marked optional.

## Step 1: personal, contact and login data (CAP-1)

| Field | Type | Values / rules |
|---|---|---|
| Title / Treatment | select | Honorific: Dr., Prof., Mr, Mrs, Ms, Eng. |
| Name | text | |
| Middle name or initial | text | **Optional** |
| Surname(s) | text | One or more surnames |
| Country / Region | select | |
| Email | email | Username. Not a disposable domain. Unique across registered users (CAP-2) and pending registrations (CAP-3) |
| Password | password | Min 12 chars; no composition rules; rejected if in a breached-password list or too similar to name/email; strength meter shown |
| Repeat password | password | Must equal Password |
| International prefix | select | |
| Mobile phone number | tel | Unique across registered users (CAP-2) and pending registrations (CAP-3) |
| How did you find out about us? | select | Search Engine (e.g. Google); Social Media (Instagram, Facebook); LinkedIn; WhatsApp; e-mail; Telephone Call; Conference or Congress; Others |
| Others: please specify | text | Shown and mandatory only when "Others" is selected |
| CAPTCHA | invisible | Challenge only when the risk check flags the attempt |

## Step 2: legal acceptance (CAP-4)

| Document | Rule |
|---|---|
| Terms | Must be accepted |
| Conditions | Must be accepted |
| Ormco TPS contract | Always shown, **unticked**, optional (for current or prospective Ormco users) |
| Commercial-use consent (CAP-13) | Always shown, **unticked**, optional. Covers MyTPDSO Limited (Ireland), MyTPDSO Spain SL (Spain) and MyTPDSO LLC-FZ (UAE), by email, WhatsApp, SMS and phone; never shared outside the group |

## Step 3: verification (CAP-5, CAP-6)

| Field | Rules |
|---|---|
| Email code | See `otp-verification.md` |
| Mobile code | See `otp-verification.md`; differs from email code |

## Step 4: completion (CAP-7)

No input. Account created, auto-login, welcome/onboarding screen.
