---
title: 'Pending bank transfers on the admin site'
type: 'feature'
ticket: '18'
created: '2026-11-25'
status: 'draft'
baseline_revision: 'fc109a4d68307af79c8ba4b8664345e120fd5230'
route: 'full'
route_source: 'auto'
risk: 'high'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A doctor who pays by bank transfer leaves the order `pending_payment` (9.17) with no way for staff to record the money arriving or to cancel the order, except a Django shell on the regional server.

**Approach:** On the admin site, a "Pending bank transfers" page reads each region's pending transfer orders live over the remote-read framework (4.5). Each row has a "Confirm payment received" button and a "Cancel order" button. They write to the region over versioned remote writes (4.6) and call `payments.service.mark_transfer_paid` / `cancel` there (one service path, AD-15). Both buttons need a fresh code (3.5) and the `sales_orders` change permission (3.6), and are audit-logged (3.8). The new resource, its access rules and its contracts go into `mytp-core` (new release), which both apps vendor.

## Decisions

- Decision (user, 2026-11-25): the list is a new list answer in mytp-core (`RemoteList`: resource, region, records each with object id, version, data); the region logs every listed order in its access log.
- Decision (user, 2026-11-25): "Confirm payment received" confirms the order total; finance types nothing. The expected version guards against a changed order; the region passes the order's own total to `mark_transfer_paid`.
- Decision (user, 2026-11-25): a row shows region, order reference, doctor name (admin's client record by uid), billing company, total, pending since, last reminder sent; never patient data.
- Decision (user, 2026-11-25): delivery DELEGATED for all three repos (one branch and one PR each): push and open the PRs; when the user says "merge", merge mytp-core first and push its `v0.34.0` tag, vendor the tag into the portal and admin PRs, then merge them; give the user read-only checks for dev over SSH; the user runs the admindev check (finance user, fresh code) in the browser; push the completion commit to master.
- Decision (user, 2026-11-25): plan kept whole (~2300 tokens).

## Boundaries & Constraints

**Always:** Live reads only, nothing kept on the admin site (AD-3.3); `Cache-Control: no-store`. No patient name or patient code anywhere on the page, in URLs, logs or audit entries (AD-17, AD-23). Mark paid and cancel stay with Financial Manager and Sysadmin (matrix `sales_orders` change); commercial roles see the list only. The confirm/cancel URL carries region, order id, expected version and idempotency key, because the step-up re-post drops the form body. The region refuses a stale version (409), and the page reloads with a message. Actor recorded as `admin_actor(<staff uuid>)`. EN only on the admin site (as its other pages).

**Never:** Refunds, partial payments, the orders view of epic-sales-finance, emails (9.8), changes to the cart or gateways, the late-transfer-on-cancelled-order policy (deferred-work :197).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| List | dev has 2 pending transfer orders, dev-ae 1 | 3 rows: region, reference, doctor, company, total, pending since | — |
| Region down | one region unreachable | other regions listed | banner names the region that could not be read |
| Confirm | financial manager, fresh code | region: order paid, one invoice, paid hooks fired; audit entry; row gone | — |
| Cancel | financial manager, fresh code | region: order cancelled, voucher and ICE released; audit entry | — |
| Stale | order changed since the list was read | — | 409: "changed meanwhile", list reloaded, nothing applied |
| Repeat | same form posted twice | applied once (idempotency key) | — |
| No permission | commercial manager | list visible, no buttons | POST: 403 |
| No fresh code | button pressed | code page, then the action | wrong code: nothing applied |
| Amount | confirm, nothing typed | region marks paid with the order's own total | — |
| Already paid | order paid meanwhile by another staff user | — | stale version, list reloaded |

</frozen-after-approval>

## Code Map

**mytp-core** (`/home/user/mytp-core`, at v0.33.0; release v0.34.0):
- `mytp_core/access.py:95-117` `_RESOURCES`: add the resource (area `sales_orders`, fields, `financial`, rules: Sysadmin and Financial Manager all + writable, commercial roles view only). It must match the admin matrix exactly (`permissions/matrix.py:225` `remote_access_mismatches`).
- `mytp_core/contracts/remote.py`: `RESOURCE_READERS` :120, `RESOURCE_WRITERS` :159 (closed changes model: `state` ∈ {paid, cancelled}), the module docstring, and the list contract `RemoteList` (records each with object id, version, data; the region's access log records every listed object). Version bump in `pyproject.toml`, README release notes (:1294 precedent). Tests in `tests/`.

**Portal** (`/home/user/mytreatplan.com`):
- `integration/remote.py:107` `remote_read`, `:235` `remote_write` (the adapter translates `payments.service.StaleVersion.current` to `integration.remote.StaleVersion`). Precedent: `integration/sample.py`, `integration/views.py:135`, `integration/urls.py`. Mounted at `/api/v1/`.
- `payments/service.py:795` `mark_transfer_paid(order, amount, *, expected_version, actor)`, `:590` `cancel(...)`, `admin_actor(uuid)`. Pending = `Payment(gateway='bank_transfer', state=OPEN, order__state=PENDING_PAYMENT)` (`payments/transfers.py` `_due` without the dates).
- `Order` fields: `id`, `reference`, `version`, `total`, `currency`, `company_code`, `pending_at`, `user.uid`.
- `requirements.txt:63` + `vendor/`: `scripts/vendor-mytp-core.sh v0.34.0` (use `MYTP_CORE_REPO=/home/user/mytp-core`).

**Admin** (`/home/user/mytp-admin`):
- `interface/remote.py:143` `read`, `:171` `write`, `RemoteReadFailed` / `RemoteWriteFailed`. Precedent view: `interface/remote_sample.py` (`readable_regions` :77, `sample_write` :211-253, `_audit` :199, `NO_STORE` :74).
- `staff/stepup.py:82` `fresh_code_required(action, label)` inside `area_required`, POST only; precedent `lists/sample.py:146`.
- `permissions/areas.py`, `permissions/urls.py` `IMPLEMENTED_AREAS` (`sales_orders` is a placeholder), `permissions/matrix.py:77-90`.
- `audit/registry.py:15` `AUDITED_VIEWS`, `audit/service.py:276` `record_remote`.
- Doctor name: `clients/models.py:36` `ClientRecord` by `uid` (AD-2 allows doctor names on the admin site).
- Templates extend `layout/base.html`. Tests use `conftest.py` `FakeRegion` :511, `make_staff_user` :78, and `staff/tests/helpers.py` `confirmed_post` :93. Vendor the wheel the same way.

## Tasks & Acceptance

**Execution:**
- [ ] mytp-core -- resource, access rules, list contract, changes model, tests, 0.34.0 release notes; tag `v0.34.0` after its PR merges.
- [ ] Portal -- vendor 0.34.0; `integration/transfers.py` (load/build/update adapters), view + URL for the read (list) and the write; tests (signed calls, access log, stale, idempotent, actor, no patient code in the answer).
- [ ] Admin -- vendor 0.34.0; area `sales_orders` implemented; `orders/` (or `interface/`) list view and confirm/cancel views with `fresh_code_required`; template; audit registry; tests with `FakeRegion` per matrix row.

**Acceptance Criteria:**
- Given a pending transfer order on dev, when a financial manager on admindev confirms it after a fresh code, then dev shows it paid with one invoice and the admin audit log has one entry naming the actor, region and order id.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Design Notes

The write is modelled as a field change (`state` to `paid` or `cancelled`) because `access.decide_write` allows only field-style changes. The portal adapter turns it into the one service call.

## Verification

**Commands:**
- mytp-core: `python -m pytest -q` -- expected 0 failures.
- Portal: `. .venv/bin/activate && python manage.py compilemessages -v 0 && python -m pytest -q -p no:cacheprovider --tb=line` (with the test FIELD_ENCRYPTION_KEYS/BLIND_INDEX_KEY of the 9.7 plan) -- expected 0 failures; `makemigrations --check --dry-run` clean.
- Admin: its pytest suite (see its CI workflow) -- expected 0 failures; `makemigrations --check` clean.
