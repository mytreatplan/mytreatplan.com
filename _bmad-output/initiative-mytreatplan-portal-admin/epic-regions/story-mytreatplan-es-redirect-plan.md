---
title: 'mytreatplan.es redirect'
type: 'feature'
ticket: '7'
created: '2026-11-02'
status: 'built'
baseline_revision: '150f06ed713ca7373f25d4c48f096fd228539cfd'
route: 'full'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** R6: mytreatplan.es and www.mytreatplan.es must answer with a 301 to mytreatplan.com, only on the production EU site. Decision (user, 2026-11-02): mytreatplan.ie and www.mytreatplan.ie get the same 301 to mytreatplan.com. The deploy tooling serves exactly one domain per site and has no way to add redirect-only names.

**Approach:** An optional per-site setting `EXTRA_DOMAINS` for `deploy/provision.sh` (space-separated names). When set, the rendered Nginx site sends those names to `https://<domain>` with a 301, path and query kept, over HTTP and HTTPS, and the site's certificate covers them. When unset, the output is the same as before apart from template comments.

## Boundaries & Constraints

**Always:**
- The redirect is a single hop on both ports: `return 301 https://__DOMAIN__$request_uri;`.
- The HTTPS redirect block has no basic auth, and ACME challenges for the extra names keep working on port 80.
- Validate `EXTRA_DOMAINS` before any change, the same way `WSGI_MODULE` is validated (exit 2, no sudo called). Each name matches the domain pattern, is not the main domain, and has no duplicates.
- One certificate (cert name = the main domain) covers the main domain and the extras. When an existing certificate lacks an extra name, provision re-issues it with `--expand` and with `-d` for every name. A name that doesn't resolve to this host means a warning, and the certificate is skipped, like the main domain today.
- No hosts, IPs or production values in the repo beyond the domain names in docs.
- Decision (user, 2026-11-02): `EXTRA_DOMAINS` comes from the environment on every provision run, like `WSGI_MODULE`; nothing is stored on the server. When it is unset but the installed site config has the redirect block, provision prints a WARNING on stderr that the run removes the redirect (and still proceeds).

**Never:**
- No change to Django (`ALLOWED_HOSTS` etc.). Redirected names never reach Gunicorn.
- No www.mytreatplan.com handling, no production nginx changes beyond this option (epic-eu-golive owns those), and no curl check of the live redirect (epic-eu-golive entry 8).
- Don't set the option on any staging site.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Unset | no `EXTRA_DOMAINS` | The rendered config has no extra server block. The `server_name` lines name only the domain. | — |
| Set | `EXTRA_DOMAINS="mytreatplan.es www.mytreatplan.es mytreatplan.ie www.mytreatplan.ie"` | Port 80 `server_name` includes all four names. An HTTPS block for all four names returns the 301 to `https://<domain>$request_uri`, with no auth_basic, using the main certificate. | — |
| Set, no cert yet | as above, before the first certificate | The HTTP-only part answers ACME for all names. certbot gets `-d` for all five (main domain plus four). | — |
| Cert lacks a name | the cert exists but lacks an extra name | certbot certonly `--expand` with all names, then a re-render. | DNS not pointing here: warning, no certbot call |
| Bad value | `"mytreatplan.es;rm"`, the main domain repeated, or a duplicate | Exit 2, "Invalid EXTRA_DOMAINS" | No sudo/stub call |

</frozen-after-approval>

## Code Map

- `deploy/nginx/site.conf.template` -- Port 80 server block (`server_name __DOMAIN__;`, ACME location, 301 to `https://__DOMAIN__$request_uri`), then the `# __HTTPS_BEGIN__` marker and the HTTPS app block (auth_basic, cert at `/etc/letsencrypt/live/__DOMAIN__/`). Add a `__EXTRA_DOMAINS__` placeholder to the port 80 server_name. Add a marked section (e.g. `# __EXTRA_BEGIN__` … `# __EXTRA_END__`) after the HTTPS marker, holding the redirect-only 443 server.
- `deploy/provision.sh` -- The header comment documents optional env vars (`CERTBOT_EMAIL`, `WSGI_MODULE`); add `EXTRA_DOMAINS` there. Validation is at lines ~40–48. `render_nginx` (~370) seds `__DOMAIN__`/`__SITE__` and cuts from `# __HTTPS_BEGIN__` when there's no certificate. It must also substitute or drop `__EXTRA_DOMAINS__` and keep or drop the extra section. The TLS step (~407) issues the cert only when `CERT_FILE` is missing, after checking that the domain resolves to a local IP (`getent ahostsv4` and `ip -4`). Extend it to all names, and to expand an existing cert (SAN check with `openssl x509 -noout -ext subjectAltName` via sudo; `openssl` is already in PACKAGES).
- `mytp_publicsite/tests/test_deploy.py` -- Reuse the patterns there: `_release_command_step`, which slices a block out of provision.sh and runs it with a `sudo() { "$@"; }` prelude; the `no_privileges` fixture; `@bash_only`. Keep these passing: `test_nginx_template_auth_redirect_and_proxy_headers` (expects `server_name __DOMAIN__;` and the existing 301 text), `test_site_templates_use_the_site_placeholder`, `test_two_sites_share_no_names`.
- `deploy/README.md` -- The provision usage section (~line 40) describes the env vars. Add a short "Extra redirect domains (production EU only)" note.

## Tasks & Acceptance

**Execution:**
- [x] `deploy/nginx/site.conf.template` -- add the placeholder and the marked redirect-only HTTPS block -- the rendered output depends only on the setting
- [x] `deploy/provision.sh` -- validate `EXTRA_DOMAINS`, render or drop the placeholder and section in `render_nginx`, make the TLS step cover and expand all names -- the single source of the behaviour
- [x] `mytp_publicsite/tests/test_deploy.py` -- tests: run the real `render_nginx` sliced from provision.sh, with stubbed sudo/install_file/nginx/systemctl into a tmp dir, both unset and set and both cert states. Check the matrix rows, including no auth_basic in the redirect block. Bad values exit 2 with no stub called. The TLS step with stubbed certbot/openssl/getent/ip gets `-d` for each name, plus `--expand` when a SAN is missing.
- [x] `deploy/README.md` -- document the option and that only the production EU site sets it, with the value `mytreatplan.es www.mytreatplan.es mytreatplan.ie www.mytreatplan.ie`

**Acceptance Criteria:**
- Given the template rendered without `EXTRA_DOMAINS`, when compared with the render from before this change, then the two match apart from comment lines.
- Given staging after merge, when the user runs `sudo nginx -t` (or provision for dev without the option), then it passes, and the dev site still answers as before.

## Design Notes

Redirect block shape:
```nginx
server {
    listen 443 ssl; listen [::]:443 ssl; http2 on;
    server_name __EXTRA_DOMAINS__;
    ssl_certificate /etc/letsencrypt/live/__DOMAIN__/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/__DOMAIN__/privkey.pem;
    return 301 https://__DOMAIN__$request_uri;
}
```

## Verification

**Commands:**
- the suite command (`local-dev-notes`) -- expected: all pass except the 3 known PostgreSQL-only failures
- `bash -n deploy/provision.sh` -- expected: no output

**Manual checks (if no CLI):**
- Staging, user: `sudo nginx -t` after running provision for dev without the option; "No changes" or a template-only change, then a reload.

## Implementation Notes

- Pre-existing bug fixed: the template's header comment line starts with `# __HTTPS_BEGIN__`, so the old HTTP-only cut (`/^# __HTTPS_BEGIN__/,$d`) removed everything from line 5 and installed a comment-only config before the first certificate. The marker regexes are now whole-line (`^# __HTTPS_BEGIN__$`, `^# __EXTRA_BEGIN__$` … `^# __EXTRA_END__$`). Sites that already have a certificate (dev, dev-ae, admindev) render byte-for-byte as before apart from comments.
- The HTTPS redirect block is rendered only once the certificate's SANs cover every extra name (`cert_missing_names`, via `sudo openssl x509 -ext subjectAltName`); port 80 always lists the extras so ACME works. This is how "then a re-render" in the matrix changes the output after `--expand`, and it avoids serving the extras with a certificate that lacks them while DNS is not ready.
- `EXTRA_DOMAINS` is lowercased and whitespace-normalised; the domain comparison and duplicate check are case-insensitive. Code paths tested locally run under macOS bash 3.2 (`sed -i.bak`, no `${var,,}`).
- Unset-vs-baseline render checked by hand against `150f06e` (identical non-comment lines, both cert states); the test compares against the template with the extra parts removed.
- Verified: `bash -n deploy/provision.sh`; full suite 1599 passed, 3 known PostgreSQL-only failures. Not done: the staging `sudo nginx -t` / provision-for-dev check (user, manual).
- Correction (review): the unset-vs-baseline match holds for the with-certificate render only. The no-certificate (HTTP-only) render differs on purpose: at `150f06e` it had no non-comment lines at all (the bug above); now it has the upstream and the port 80 server. dev, dev-ae and admindev all have certificates, so they never take that path.
- Review patch: the "EXTRA_DOMAINS is not set" warning also fires when the installed config lists more than one name on a `server_name` line (extras on port 80 before the certificate covers them), not only on the `# __EXTRA_BEGIN__` marker; test cases added for that state and for a plain single-domain config.

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, 2026-11-02): high 0, medium 0, low 3, false 0, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | AC "render unset = before, apart from comments" not met for the no-certificate render; Implementation Note claimed both states matched | low | rejected | Real: `150f06e`'s HTTP-only cut left 0 non-comment lines (a pre-existing bug); the new render is the intended working config. No site on staging takes that path. The only fix is to the plan's note, corrected above; reported to the user. |
| 2 | `test_without_extra_domains_the_site_renders_as_before` derives "before" from the modified template, not the baseline | low | rejected | Real, but an unrelated non-comment change in the template is still caught by `test_nginx_template_auth_redirect_and_proxy_headers` and the other pinned-string tests; comparing to a git revision would break on CI's shallow checkout. |
| 3 | The "run without EXTRA_DOMAINS" warning misses an installed config with extras only on port 80 (certificate not yet covering them, or HTTP-only) | low | patch | Real: the check only looked for `# __EXTRA_BEGIN__`. Patched: also matches a multi-name `server_name` line; tests added (94 passed in test_deploy). |

