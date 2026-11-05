---
title: 'Refactor sweep'
type: 'refactor'
ticket: '13'
created: '2026-11-05'
status: 'done'
baseline_revision: '9ebf3535ef384dca312d641ddebecf0e55f11882'
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

**Problem:** Epic-admin-access (entries 1–12) left duplicated helpers in mytp-admin — the htmx "go on after a POST" helper copied five times, and test helpers copied or imported across apps' test modules — plus four deferred entries whose status is out of date.

**Approach:** Behaviour-preserving cleanup in mytp-admin only: one shared htmx helper module, shared test helpers and fixtures in conftest, and an updated status for each epic-admin-access entry in `deferred-work.md`. No change in what any page, command or test checks.

## Boundaries & Constraints

**Always:**
- Behaviour-preserving: every existing test passes unchanged in what it asserts; only imports and helper locations move. No template output changes.
- `layout/htmx.py` (new): `is_htmx(request)` and `go_to(request, url)` (`HX-Redirect` for htmx, else a 303), replacing `staff/admin_users.py:296,300`, `staff/stepup.py:123,125`, `staff/profile.py:70,74`, `lists/sample.py:127,183`, `inbox/views.py:85,88`, and the inlined checks at `lists/views.py:268`. Same status codes and headers as today.
- Test helpers: the sign-in/fresh-code helpers (`step_up_code`, `login_code`/`current_code`, `wrong_code`, `confirmed_post`/`confirm`) in one module (`staff/tests/helpers.py`); fixtures used by several apps (`clock`, `sentry_messages`, `backdate`, `login`, `region`, `sysadmin`, `signed_in`) in the root `conftest.py`, so no test imports another app's test module. Where two copies differ (audit's `region` also creates a missing region), keep the more general one.
- `deferred-work.md`: append one status entry per affected item (existing entries are never edited): provider-by-class — admin side done (`staff/checks.py:34-46`), portal remaining; browser tests for htmx lists — open; mytp-core `fresh_code` wording — open, with the next core release the admin vendors (entry 4.3), then remove the workaround comment at `staff/signin.py:121-125`; separate Twilio Verify for fresh codes — open (needs the user's Twilio service).

- Decision (user, 2026-11-05): the three htmx form partials (`lists/_sample_form.html`, `staff/_admin_users_form.html`, `staff/_profile_form.html`) stay as they are (no shared include: small gain, no browser tests).
- Decision (user, 2026-11-05): the mytp-core `fresh_code` wording fix and the removal of the admin workaround comment go with entry 4.3 (the admin's next mytp-core vendoring), recorded open in `deferred-work.md`.

**Never:**
- No mytp-core change, no new behaviour, no List demo removal (epic-sales-finance), no browser-test infrastructure.

</frozen-after-approval>

## Code Map

- Repository: mytp-admin at `/Users/jadeandres/Library/CloudStorage/GoogleDrive-mytpdso@gmail.com/Mi unidad/Desarrollo Portal MyTreatplan/code/mytp-admin`, branch `story/3.13-refactor-sweep` (baseline in frontmatter); this plan stays in the planning worktree. Test command: `/private/tmp/claude-501/admin-venv/bin/python -m pytest -q -p no:cacheprovider` with `SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver DATABASE_URL=sqlite:///$TMPDIR/adm.sqlite3 FIELD_ENCRYPTION_KEYS=test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE= BLIND_INDEX_KEY=dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU= ALLOW_FAKE_VERIFICATION=True` (sandbox off). Do not commit or push.
- htmx helper copies: listed in Boundaries (file:line).
- Test helper copies: `staff/tests/test_admin_users.py:35,39,43`, `staff/tests/test_step_up.py:38,43,62`, `staff/tests/test_sign_in.py:32,37`; cross-app imports `inbox/tests/test_inbox.py:20` (from `staff.tests.conftest`), `staff/tests/test_profile.py:13` (from `audit.tests.test_audit`); duplicated `login`/`region` `lists/tests/test_lists.py:43,77` and `audit/tests/test_audit.py:41,55`; `sysadmin`/`signed_in` `inbox/tests/test_inbox.py:36,41`, `staff/tests/test_profile.py:24,30`, `layout/tests/test_pages.py:18`, `staff/tests/conftest.py:30`.
- Root `conftest.py` (`pytest_configure` :12-56, `clear_cache` :59, `make_staff_user` :68, `staff_user` :83).
- `deferred-work.md`: `mytreatplan.com/_bmad-output/initiative-mytreatplan-portal-admin/deferred-work.md` (written on portal master with this plan after the merge).

## Tasks & Acceptance

**Execution:**
- [ ] `layout/htmx.py` (new) and the five view modules -- one helper.
- [ ] `staff/tests/helpers.py` (new), root `conftest.py`, the test modules -- shared helpers and fixtures, no cross-app test imports.
- [ ] `deferred-work.md` status entries (coordinator, on portal master).

**Acceptance Criteria:**
- Given the admin suite, when run before and after, then the same number of tests pass (plus none lost) and no test module imports from another app's `tests` package.
- Given `grep -rn "def _go_to\|def _is_htmx\|def _redirect" --include=*.py`, then nothing remains outside `layout/htmx.py`.

## Implementation Notes

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, opus, behaviour-preservation focus, 2026-11-05): 5 findings, no behaviour change found (749 passed on the baseline and on the change). low 5: 3 patched, 2 rejected.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `backdate`, `login`, `region` are plain functions in the root conftest, imported with `from conftest import …` (the plan says fixtures; pytest discourages importing conftest) | low | rejected | Works from the root, from the app folders and with `--import-mode=importlib` (reviewer ran all); recorded in Implementation Notes. |
| 2 | Shared `region()` uses get_or_create (a typo no longer fails in lists tests) | low | rejected | The plan says to keep the more general copy. |
| 3 | `signed_in` is both a root fixture (client) and a helper function (bool) | low | patch | Function renamed `is_signed_in`. |
| 4 | `layout/middleware.py:49` still inlines the htmx header check | low | patch | Uses `is_htmx`. |
| 5 | Four blank lines in `audit/tests/test_audit.py` | low | patch | Two. |

Verification after the patches: admin suite 749 passed (same as the baseline).

## Verification

**Commands:**
- the admin suite -- all pass, same count as the baseline (record both).

## Staging

- Merged in mytp-admin PR #13 (2026-11-05, merge 2a3c767). PR CI green on PostgreSQL (run 42034485019). No behaviour change, so no server check beyond the deploy (admindev keeps working). Local admin suite: 749 before and after.
