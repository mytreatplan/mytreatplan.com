---
title: 'Django 6.1.2 security release'
type: 'chore'
ticket: '3'
created: '2026-10-29'
status: 'built'
baseline_revision: '9d137f33d5feba635e0abf55c6ad2e89d60730e8'
route: 'oneshot'
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

**Problem:** The project pins Django 6.1.1. Django 6.1.2 (6 October 2026, the latest release on PyPI) fixes four security issues (CVE-2026-77050, CVE-2026-84429, CVE-2026-87890, CVE-2026-87975) and several 6.1 regressions.

**Approach:** Change the pin in `requirements.txt` to 6.1.2, run the full suite, and fix anything the release notes require. Nothing in the code uses the affected features that change behaviour (GeoDjango, model formsets, `parse_header_parameters`), so no code change is expected.

</frozen-after-approval>

## Implementation Notes

Oneshot route: a one-line pin change. Investigation against the 6.1.2 release notes:
- The two backwards-incompatible changes are GeoDjango raster lookups (not installed) and the undocumented `django.utils.http.parse_header_parameters` (not called anywhere in the repo).
- CVE-2026-87975 (model formsets with editable or UUID primary keys) matters for part 2's new UUID-key tables (AD-5); no formsets exist yet, so 6.1.2 is in place before any are written.
- CVE-2026-84429 (header parsing DoS) and CVE-2026-77050 (long language codes) affect every Django site; both fixes come with the upgrade.

- Changed: `requirements.txt` (Django 6.1.1 → 6.1.2). Local venv updated.
- Correction (review): one model formset already exists. The Django admin inline `EventInline` (`eventlog/admin.py`) under `AttemptAdmin`, whose parent `Attempt` has a UUID primary key, is the CVE-2026-87975 code path. It is read-only (ReadOnlyMixin, all fields read-only, `extra = 0`, `can_delete = False`), so no formset data is submitted; 6.1.2 closes the path anyway. No code change needed.
- Suite on SQLite: 1461 passed, 3 failed. The 3 are the known PostgreSQL-only tests in `accounts/tests/test_crypto.py` (raw SQL); they fail identically on 6.1.1 (two bind a UUID through sqlite3, one calls `now()`), so nothing new. `manage.py check`: no issues.

## Plan Change Log

## Review Triage Log

- Pass 1 (quick): 1 finding; low 1. Patched.
  - low, patch: the Implementation Notes said no formsets exist; `eventlog/admin.py` `EventInline` (inline formset, UUID-keyed parent) does. Verified in the file; it is read-only, so no exposure. Note corrected; no code change.

## Verification

**Commands:**
- Full suite with the local test keys and SQLite (local-dev notes) -- expected: all pass except the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py`.
- `python manage.py check` -- expected: no issues.

**Manual checks (if no CLI):**
- After merge, the staging deploy succeeds and dev's `/healthz` answers 200.
