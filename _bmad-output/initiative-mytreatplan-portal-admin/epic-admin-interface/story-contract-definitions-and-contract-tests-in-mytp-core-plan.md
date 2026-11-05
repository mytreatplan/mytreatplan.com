---
title: 'Contract definitions and contract tests in mytp-core'
type: 'feature'
ticket: '2'
created: '2026-11-05'
status: 'built'
baseline_revision: '2209447887e78e023d8d53c4c00ec9408cfac573'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** I3 (AD-12, AD-23): entries 3–9 will exchange events, errors and amounts between the admin site and the portals, but no shared definition of those shapes exists, nor a check that keeps patient data and floats out of central payloads.

**Approach:** mytp-core 0.7.0 adds `mytp_core.contracts`: pydantic 2 models for the error body (entry 1's `{"error": {"code", "message"}}`, which `interface.refusal_body` then builds through), the amount `{"amount": "123.45", "currency": "EUR"}`, and the event envelope `{event_id, type, version, aggregate_id, aggregate_version, sequence, occurred_at, region, payload}`; the version policy (n and n−1 served, readers ignore unknown fields and event types); JSON Schema files exported from the models; and contract checks that refuse patient fields and floats in central payloads. No concrete event payloads (each later entry adds its own).

## Boundaries & Constraints

**Always:**
- pydantic 2, pinned `==` in mytp-core (pyproject range + `requirements-test.txt` pin). Models are readers' models: `extra='ignore'` (unknown fields dropped, never an error); writers produce JSON with `model_dump(mode='json')`.
- Error: `code` (a short snake_case string) and `message`; entry 1's 401 reason codes and the sites' 503 `unavailable` become named constants here.
- Amount: `amount` is a JSON **string** of a decimal with exactly 2 places (`"123.45"`, `"-5.00"`), parsed to `Decimal`; a JSON number (int or float) is refused; `currency` is 3 uppercase letters (ISO 4217 form). Money itself is epic-catalogue entry 1.
- Envelope: `event_id` UUID string; `type` dotted lowercase (`client.updated`); `version` int ≥ 1; `aggregate_id` UUID string; `aggregate_version` int ≥ 1; `sequence` int ≥ 1; `occurred_at` ISO UTC ending in `Z`; `region` a region code; `payload` an object. An unknown `type` still parses (a reader skips it).
- Versions: `API_VERSION = 1` and `served_versions()` → (n, n−1 when n > 1); a helper that builds the `api/v<n>/` URL includes for the served versions from a mapping, used by the sites in later entries.
- Contract checks (`mytp_core.contracts.checks`), used by the tests here and by the sites' tests later: `check_central_payload(obj)` walks a payload and refuses (a) any float anywhere, (b) any key on the patient-field list, (c) any file key or file-like value, per the decisions below, at any depth. Envelope validation runs both checks on `payload`.
- JSON Schema: one file per model under `mytp_core/schemas/` (included in the wheel via package-data), and a test that fails when a file differs from `Model.model_json_schema()`; a command `export_contract_schemas` rewrites them.
- Release 0.7.0 per README "Releasing"; README: a "Contracts" section and the compatibility rule.
- Decision (user, 2026-11-05): patient fields are a fixed key list refused at any depth — `patient_name`, `patient_first_name`, `patient_last_name`, `patient_photo`, `patient_comments`, and inside any object under the key `patient` every key except `patient_code`. Doctor and clinic names stay allowed.
- Decision (user, 2026-11-05): uploaded files holding patients' medical data never go central either. Refused at any depth: the keys `file`, `files`, `filename`, `file_name`, `attachment`, `attachments`, `upload`, `uploads`, `object_key`, `storage_key`, `download_url`, `presigned_url`; and any string value that is a `data:` URI or a presigned object-storage URL (contains `X-Amz-Signature` or `X-Amz-Credential`, any case). A later need for a file reference centrally (e.g. an invoice PDF) is an explicit, reviewed allowlist entry in mytp-core, not a bypass.
- Decision (user, 2026-11-05): the JSON Schema files are committed in `mytp_core/schemas/`, shipped in the wheel, guarded by a drift test, rewritten by `export_contract_schemas`.
- Decision (user, 2026-11-05): mytp-core gives the version policy and the URL-include helper, tested with a sample v2 in `tests/testapp`; the sites use it when they first need it.
- Decision (user, 2026-11-05): no site change in this story; 0.7.0 is tagged here and both sites vendor it in entry 3 (outbox and ingest), where pydantic arrives with the first use.

- Decision (user, 2026-11-05, after review 2): the key list is a safety net, broadened: refused (after normalising keys: NFKC, invisible characters removed, strip, lowercase, `_` `-` `.` and spaces removed) any key starting with `patient` other than `patient_code`; `patient_code` anywhere must be a string in the AD-5 form (`^[A-Z]{2}-[0-9A-Z]{8}$`); also refused: links to object-storage hosts (`*.amazonaws.com`, `*.digitaloceanspaces.com`, `*.cloud.ovh.net`, `storage.googleapis.com`), signed links of any common scheme (X-Amz-Signature/Credential, AWSAccessKeyId+Signature, X-Goog-Signature/Credential), data URIs anywhere (by their grammar `data:<type>/<subtype>…,`, so ordinary text starting "Data:" passes), and long base64-only strings (200+ characters). The guarantee comes from closed payload models: every event type added by later entries (3 outbox, 8 client sync, 9 contact messages and after) defines its payload as a model that refuses unknown fields on the writer side (`extra=forbid`), checked by `check_central_payload` too; readers still ignore unknown fields.

**Never:**
- No site code in this story (the sites adopt 0.7.0 in entry 3, per decision). No concrete payload models. No change to entry 1's wire format.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Valid envelope | all fields | parses; dumps back to the same JSON | — |
| Unknown field | envelope + `"extra": 1` | parses, field dropped | — |
| Unknown type | `type: "x.new"` | parses | reader skips |
| Patient name in payload | `{"patient_name": "Ana"}` (any depth) | refused | ValidationError names the path |
| File in payload | `{"attachments": [...]}`, `{"x": "data:application/pdf;base64,…"}`, a presigned URL value | refused | ValidationError names the path |
| Float anywhere | `{"total": 12.5}` or amount `12.5` | refused | ValidationError |
| Amount forms | `"12.5"`, `"12.345"`, `12`, `"EURO"` | refused | ValidationError |
| Version n−1 | `served_versions()` with n=2 | (2, 1); both URL includes built | — |
| Schema drift | model changed, file not | test fails | rerun the export command |

</frozen-after-approval>

## Code Map

- Repository: mytp-core at `/Users/jadeandres/Library/CloudStorage/GoogleDrive-mytpdso@gmail.com/Mi unidad/Desarrollo Portal MyTreatplan/code/mytp-core`, branch `story/4.2-contracts` (baseline in frontmatter). Do not commit, tag or push. This plan file stays in the planning worktree; record Implementation Notes here. Tests: `../.venv/bin/python -m pytest -q -p no:cacheprovider` from the mytp-core folder (sandbox off); install pydantic into `../.venv` with pip if missing, pinned as in requirements-test.txt.
- `../mytp-core/mytp_core/interface.py` -- `REASONS` :86-95, `refusal_body(code)` :130-132, `PeerCallFailed` :120-127: build the body through the new error model, keep the wire format.
- Sites' 503 body is hand-built (admin `interface/calls.py:66-75`, portal `integration/calls.py`): left as is here; entry 3 switches them.
- `../mytp-core/pyproject.toml` -- version :7, deps :12-16, `packages.find include=["mytp_core*"]` (add `[tool.setuptools.package-data] mytp_core = ["schemas/*.json"]`); `requirements-test.txt` (pins; `typing_extensions==4.16.0` already).
- `../mytp-core/README.md` -- module list :90, "Compatibility rules" :231, "Releasing" :267.
- Tests: `tests/test_<module>.py`, `tests/settings.py`, `tests/testapp/` (for the URL helper test).
- Architecture: AD-6 (:124-128, amounts never floats; JSON `{"amount": "123.45", "currency": "EUR"}` at :250), AD-12 (:168), AD-2 (:85-97, patient data = names, photo, comments; central copies use `patient_code` only, AD-5 :122), AD-23 (:235), conventions :249-251 (UUID strings, ISO UTC `Z`).

## Tasks & Acceptance

**Execution:**
- [x] `mytp_core/contracts/__init__.py`, `errors.py`, `amounts.py`, `events.py`, `versions.py`, `checks.py` -- the models, policy and checks.
- [x] `mytp_core/schemas/*.json`, `management/commands/export_contract_schemas.py` -- exported schemas and their command.
- [x] `mytp_core/interface.py` -- `refusal_body` through the error model (same output).
- [x] `tests/test_contracts.py` -- every matrix row, the schema drift test, `refusal_body` unchanged.
- [x] `pyproject.toml`, `requirements-test.txt`, `mytp_core/__init__.py`, `README.md` -- pydantic, package data, 0.7.0, docs.

**Acceptance Criteria:**
- Given the built wheel, when it is installed, then `mytp_core/schemas/*.json` are inside it.
- Given entry 1's tests, when run on 0.7.0, then they pass unchanged.

## Implementation Notes

- pydantic pinned `==2.14.3` (latest on 2026-11-05) in pyproject; requirements-test.txt also pins its dependencies `pydantic_core==2.50.4`, `annotated-types==0.8.0`, `typing-inspection==0.4.5` (typing_extensions 4.16.0 already there). Installed into `../.venv`.
- Added `mytp_core/contracts/base.py` (not in the task list): `ContractModel` with `extra='ignore'`, `frozen=True`, shared by the three models.
- Error: `code` pattern `^[a-z][a-z0-9_]{0,63}$`, `message` max 500 chars. `REASONS` in interface.py is now keyed by the named constants; `refusal_body` builds through `error_body` (same dict; a code outside the pattern would now raise ValidationError, but only REASONS codes and `other`-style snake_case codes are ever passed). `errors.REFUSAL_CODES` and `UNAVAILABLE` added.
- Amount: pattern `^-?(0|[1-9][0-9]{0,14})\.[0-9]{2}$` (no leading zeros, at most 15 integer digits); a Python `Decimal` with exponent -2 is accepted for writers; JSON dump gives the string, Python dump keeps the Decimal.
- Envelope: UUID fields as `uuid.UUID` (dump as canonical strings); `version`/`aggregate_version`/`sequence` are StrictInt >= 1 (a `"1"`, `1.0` or `true` is refused); `occurred_at` must be a string ending `Z` (or an aware UTC datetime in Python), dumped with `Z` and microseconds only when non-zero; `region` pattern `^[a-z]{2}$` (form only; the list of regions stays in the portal's profiles).
- Checks: keys compare case-insensitively; every violation is collected (`ContractViolation.violations`, message lists `path: reason`); bytes/bytearray/memoryview/IO values count as files; the `patient` rule also applies to objects in a list under `patient`. No allowlist mechanism yet (none needed; the docstring and README say an entry is a reviewed mytp-core change).
- Versions: `api_url_patterns(urlconfs, current=API_VERSION, prefix='api/')` returns `path('api/v<n>/', include(...))` newest first; a missing served version raises ImproperlyConfigured, older mapping entries are ignored. Sample v1/v2 in `tests/testapp/api.py` (`UrlsV1`, `UrlsV2`, used as ROOT_URLCONF in the test).
- Schemas: `amount.json`, `error_body.json`, `event_envelope.json`, written with `json.dumps(sort_keys=True, indent=2)` + newline (`contracts.schema_text`). `export_contract_schemas` has `--check` (compare only) and `--dir`. Package data `mytp_core = ["schemas/*.json"]`.
- README: module entry, command entry, a "Contracts" section, 0.7.0 in "Compatibility rules" plus the contract compatibility rule. Version 0.7.0 in pyproject and `__init__`. Not committed, tagged or pushed.
- Verification: `../.venv/bin/python -m pytest -q -p no:cacheprovider` → 627 passed (SQLite; entry 1's test_interface unchanged and passing). `python -m build --wheel` (build 1.3.0 in a temporary venv) → `mytp_core-0.7.0-py3-none-any.whl` holds `mytp_core/schemas/{amount,error_body,event_envelope}.json` and `Requires-Dist: pydantic==2.14.3`. PostgreSQL run left to CI.
- Review round 1 (bypass probes, fixed in place): the patient rule covers a string or list under `patient` and needs `patient_code` to be a string; only JSON-shaped values pass (dict, list, tuple, str, int, bool, None; Decimal, sets, models, dataclasses and other mappings refused); keys compare normalised; presigned parameters are found after percent-decoding; data URIs inside text and behind a BOM or zero-width character are refused; a dict with `amount` and `currency` must be a valid Amount; the envelope refuses forbidden unknown top-level fields and re-checks `payload` on dump; `occurred_at` must be `YYYY-MM-DDThh:mm:ss[.ffffff]Z`; UUIDs only in canonical lowercase hyphenated form; `-0.00` refused; the schema's `payload` has a description of the rules; README updated.
- Review round 2 (broadened safety net, per the frozen decision "after review 2"): keys normalised with NFKC, Cf characters removed and `.` dropped as well, through `str.__str__` so str subclasses cannot change the result; any key starting with `patient` but `patient_code` refused; `patient_code` anywhere must match `^[A-Z]{2}-[0-9A-Z]{8}$`; object-storage host links, AWSAccessKeyId+Signature and X-Goog signed links refused; decoding repeats until stable, and a value still changing after 20 rounds is refused; data URIs found by a bounded grammar anywhere (text starting "Data:" now passes); 200+ characters of base64 only refused; depth capped at 64 containers, cycles refused, shared containers checked once per context; the envelope re-validates every field on dump (`model_copy`, `model_construct`); README says closed writer-side payload models are the guarantee. Behaviours to know: `patients` holds objects only (`[{"patient_code": ...}]`, never bare codes); any long base64-only string is refused, even a long token; payloads nest at most 64 levels.
- Review round 3: a container shared between a plain path and `patient`/`patients` must be checked in both contexts. The walk already memoised by (container, context), so only a regression test was added. `tests/test_contracts.py`: 206 passed.

## Plan Change Log

- 2026-11-05, review pass 2 (bypass probes): a name-based list cannot be complete (synonyms, public object URLs, raw base64). The user chose broadened heuristics plus closed writer-side payload models in later entries (frozen decision "after review 2"). Patched in place; KEEP the review-1 fixes and tests.

## Review Triage Log

Pass 1 (quick lens, opus, bypass-focused, 2026-11-05): 15 findings. high 3, medium 6, low 4, false 2.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `{'patient': 'Ana'}` / a list of strings under `patient` passes | high | patch | Confirmed by the reviewer's probe (also raised by the implementer). Only an object or list of objects is accepted under `patient`. |
| 2 | An object under `patient_code` escapes the patient rule | medium | patch | `patient_code` must be a string. |
| 3 | Non-dict/list/tuple values (pydantic models, dataclasses, sets, mappings) pass unchecked and are dumped | high | patch | Every type other than dict/list/tuple/str/int/bool/None is refused (Decimal included). |
| 4 | Key spelling variants (camelCase, `-`, stray spaces) pass | high | patch | Keys normalised (strip, lowercase, drop `_`/`-`/spaces) before comparing. |
| 5 | Percent-encoded `X-Amz-*` passes | medium | patch | Repeated percent-decoding before matching. |
| 6 | data: URIs embedded in text or BOM/zero-width-prefixed pass | medium | patch | `data:…;base64,` refused anywhere; invisible prefixes stripped. |
| 7 | Forbidden keys at the envelope's top level are dropped silently | medium | patch | A before-validator checks unknown top-level keys. |
| 8 | `payload` mutable after validation bypasses the check | medium | patch | Re-checked on serialization. |
| 9 | Amounts inside payloads not shape-checked | medium | patch | A dict with `amount` and `currency` must validate as Amount; Decimal values refused. |
| 10 | `occurred_at` accepts non-RFC-3339 forms | low | patch | Strict pattern. |
| 11 | Lax UUID spellings accepted | low | patch | Canonical lowercase pattern. |
| 12 | `-0.00` accepted | low | patch | Refused. |
| 13 | The schema's `payload` reads as "anything goes" | low | patch | Description added; README documents the writer rule. |
| 14 | Drift test | false | rejected | Reviewer found no defect. |
| 15 | Entry 1's wire format | false | rejected | Unchanged for every code in use; `refusal_body` now refuses malformed codes, none of which is passed in-repo. |

Pass 2 (opus, bypass probes on the patched code, 2026-11-05): 8 findings. The name-based list cannot be complete (synonyms, public object URLs, raw base64): intent_gap, resolved by the user (frozen decision "after review 2"): broadened heuristics plus closed writer-side payload models in later entries. Patched: key normalisation (NFKC, invisible characters, `.`, str subclasses), every `patient*` key except an AD-5 `patient_code` (checked anywhere), object-storage hosts, SigV2/GCS signed links, percent-decoding until stable (20 rounds), data URIs by grammar anywhere (the `Data:` false positive gone, bounded pattern: the quadratic-time case fixed, 1 MB in < 1 s), long base64-only strings, depth 64 and cycles as violations, the whole envelope re-validated on every dump (`model_copy`/`model_construct`). Accepted as designed: an object with both `amount` and `currency` must be a valid Amount. A follow-up shared-container case was already covered (regression test added).

Verification: mytp-core 731 passed; CI green on master and on tag v0.7.0 (runs 42028673981, 42028673793).

## Verification

**Commands:**
- in `../mytp-core`: `../.venv/bin/python -m pytest -q -p no:cacheprovider` -- all pass; `python -m build --wheel` then list the wheel -- schemas present.
