# Privacy notes for the lawyer

Prepared on 2026-11-04 (epic-regions, story "Region suggestion bar") for the review of the
public privacy text. The public text
(`mytp_publicsite/templates/publicsite/legal/_privacy_body.html`) says the site places no
cookies; that is already not true and stays unchanged until the lawyer answers.

Read from the code: `mytreatplan/settings.py`, Django's defaults, `accounts/devices.py`
(with `mytp_core.devices` and `mytp_core.policy.PORTAL`), `accounts/sessions.py` and
`regions/suggestion.py`. "Secure" below means the cookie is sent over HTTPS only; on the
servers (DEBUG off) every cookie that has the flag set to "as the session" is Secure.

## Cookies

| Cookie | Set by / when | Purpose | Lifetime | Flags |
|---|---|---|---|---|
| `csrftoken` | Django, on any page with a form (contact form, login, signup) | Protection against cross-site request forgery: the form's token is checked against it. Holds a random value, no personal data. | 1 year (Django's `CSRF_COOKIE_AGE` default, 31,449,600 s) | Secure on the servers; SameSite=Lax; not HttpOnly (Django's default) |
| `sessionid` | Django, when a session is needed: login, the signup wizard, the pending login | Keeps the visitor's session (the logged-in account, the signup steps). Holds a random key; the data stays on the server. | Anonymous session: 2 weeks (Django's `SESSION_COOKIE_AGE` default). Logged-in session: renewed on each request to 1 hour; the session ends after 15 minutes idle (`mytp_core.policy.PORTAL`) | Secure and HttpOnly on the servers; SameSite=Lax |
| `mtp_device_<digest>` (remembered device) | The login code page, only when the Doctor ticks "Remember me on this device/browser for 90 days" | Skips the second login step on this browser. A signed value with the account id, the issue time and a hash tied to the password; the name is a keyed digest of the account id. No email or phone. | 90 days from when it is given (not extended) | Secure on the servers; HttpOnly; SameSite=Lax |
| `mytp_stay_region` | The region suggestion bar's "Stay here" (or its close link), only when the visitor clicks it | Remembers that the visitor chose to stay on this region's site, so the bar is not shown again. Value `1`, nothing else. | 1 year | Secure on the servers; HttpOnly; SameSite=Lax; path `/` |
| `messages` | Django's messages framework (default storage: the cookie first, the session only when a notice is too large for it), whenever a one-time notice is queued, e.g. the idle-logout notice (`accounts/sessions.py`) or "password changed"; removed once shown | Shows a notice on the next page. Removed once shown. | Browser session | Secure on the servers; HttpOnly; SameSite=Lax (Django's session-cookie settings) |

No analytics, advertising or third-party cookies are set. Visiting the home page sets
`csrftoken` (the page carries the contact form); the legal pages set none; the others are
set only by the actions in the table.

## Geo-IP lookup (the region suggestion bar)

- On the home page and the legal pages, the server looks the visitor's IP address up in a
  local copy of the DB-IP Lite country database (CC BY 4.0), on the same instance, to find
  the country only. No IP address is sent to DB-IP or anyone else; the database file is
  downloaded monthly.
- The result is used only to decide, for that one request, whether to show a bar
  suggesting the other region's site (mytreatplan.com / mytreatplan.ae). There is no
  automatic redirect.
- Nothing is stored or logged: neither the IP address nor the country, for this purpose.
  (The portal's own security records of logins and page requests are a separate matter
  and are already covered elsewhere.)
- Crawlers and visitors who chose "Stay here" are not looked up.
- The footer of every page credits DB-IP ("IP geolocation by DB-IP", linking to
  https://db-ip.com), as its licence requires.

## Questions for the lawyer

1. Does the privacy text need to list these cookies, and in what form (table as above)?
2. Are `csrftoken`, `sessionid`, the remembered-device cookie and `mytp_stay_region`
   strictly necessary (no consent banner needed), as we assume? `mytp_stay_region` is set
   only on the visitor's own click.
3. Does the transient, local geo-IP lookup need a mention, and under which legal basis?
