---
title: 'Money type in mytp-core'
type: 'feature'
ticket: '1'
created: '2026-11-05'
status: 'built'
baseline_revision: '540127c98e83542f363f1ccd3932abf624be4c71'
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

**Problem:** AD-6: prices, invoices and the accounting feed need one exact money type on every site; today only 4.2's wire shape (`Amount`) exists and nothing prevents floats, mixed currencies or ad-hoc rounding in the code that will compute prices.

**Approach:** mytp-core gains `mytp_core.money.Money`: an immutable value of a `Decimal` quantized to 0.01 (ROUND_HALF_UP) and an ISO 4217 code among EUR, AED, USD; arithmetic only within one currency; conversion to and from 4.2's `{"amount": "123.45", "currency": "EUR"}` shape. Released as a new minor and vendored into both sites, which import it.

## Boundaries & Constraints

**Always:**
- Construction from `Decimal`, `int` or a decimal string only; `float` (and `bool`) refused with `TypeError`; NaN/Infinity refused; the value is quantized half up to 2 places (`Money('1.005', 'EUR')` → `1.01`); currency must be one of `CURRENCIES = ('EUR', 'AED', 'USD')`, else `ValueError`.
- Immutable and hashable; ordering (`<`, `<=`, `>`, `>=`) only between the same currency (different currencies raise `CurrencyMismatch`, a `ValueError`); `==` between different currencies is `False` (decision, user, 2026-11-05, after review: raising from `==` breaks `in`, dict lookups and ordinary equality checks); `Money` never equals a bare number.
- `+`, `-` with another `Money` of the same currency (else `CurrencyMismatch`); unary `-`; `sum()` works with a `Money.zero(currency)` start; operations with numbers other than per the multiplication decision are `TypeError`.
- `str()` → `"123.45 EUR"`; `repr()` unambiguous; `.amount` (Decimal), `.currency`.
- JSON: `to_contract()` → `{"amount": "123.45", "currency": "EUR"}` validated by `contracts.Amount`; `Money.from_contract(dict_or_Amount)`; the round trip is exact; `-0.00` normalised to `0.00`.
- Release: the next minor after 4.3's 0.8.0 (0.9.0), built on a mytp-core worktree branched from master and rebased onto 0.8.0 before tagging; README section "Money"; both sites vendor it once 4.3's site PRs are merged (a small PR each: the wheel, requirements, and an import test).

- Decision (user, 2026-11-05): `Money * n` and `n * Money` for `int` or `Decimal` n (quantities, percentages, VAT rates), the exact product quantized half up to cents; `split(n)` (n ≥ 1 int) returns n Money of the same currency summing exactly to the original, the leftover cents given one each to the first parts (negative amounts mirror it); no `/` operator.
- Decision (user, 2026-11-05): no Django model field in this story; epic-catalogue entry 2 decides storage.

**Never:**
- No currency conversion (rates are epic-catalogue entry 13), no Django model field (per decision), no float anywhere in the module.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Float | `Money(1.5, 'EUR')` | refused | TypeError |
| Half up | `'1.005'`, `'-1.005'`, `'2.675'` | `1.01`, `-1.01`, `2.68` | — |
| Mixed add | EUR + AED | refused | CurrencyMismatch |
| Unknown currency | `'GBP'`, `'eur'` | refused | ValueError |
| Round trip | `to_contract` → `from_contract` | equal Money | — |
| Contract input | `{"amount": 12.5, …}` or `"12.5"` | refused (4.2's Amount rules) | ValidationError |
| Sum | `sum([...], Money.zero('EUR'))` | exact total | — |
| Multiply | `Money('10.00','EUR') * Decimal('0.21')`, `* 3`, `* 1.5` | `2.10`, `30.00`, refused | float → TypeError |
| Split | `Money('10.00','EUR').split(3)` | `3.34, 3.33, 3.33` | n < 1 → ValueError |

</frozen-after-approval>

## Code Map

- Repository: the mytp-core worktree `/Users/jadeandres/Library/CloudStorage/GoogleDrive-mytpdso@gmail.com/Mi unidad/Desarrollo Portal MyTreatplan/code/mytp-core-5.1`, branch `story/5.1-money` (baseline in frontmatter). Story 4.3 is being built in the main `mytp-core` checkout at the same time: do not touch it. Do the mytp-core part only (money.py, tests, README section); leave the version at 0.7.0 — the coordinator bumps it to 0.9.0 after rebasing onto 0.8.0; the site vendoring task is the coordinator's, later. Do not commit, tag or push. Tests: `../.venv/bin/python -m pytest -q -p no:cacheprovider` from the worktree folder (sandbox off). This plan file stays in the planning worktree; record Implementation Notes here.
- mytp-core (`.../code/mytp-core`): `contracts/amounts.py` (`Amount`, `AMOUNT_PATTERN`, exactly 2 places, no `-0.00`) — `to_contract`/`from_contract` go through it; new `mytp_core/money.py`; tests `tests/test_money.py`; `pyproject.toml` version, `mytp_core/__init__.py`, README module list and "Compatibility rules".
- Architecture AD-6 (`architecture-portal-admin.md:124-128`): Decimal quantized to 0.01 plus ISO 4217 (EUR, AED, USD), never a float; JSON shape at :250.
- Sites: `scripts/vendor-mytp-core.sh`, `requirements.txt`; a one-line import test in each (`from mytp_core.money import Money`).

## Tasks & Acceptance

**Execution:**
- [x] `mytp_core/money.py`, `tests/test_money.py` -- the type and every matrix row.
- [ ] README, version -- 0.9.0 after 0.8.0.
- [ ] Portal and admin -- vendor 0.9.0 and an import test (after 4.3's site PRs).

**Acceptance Criteria:**
- Given both sites on 0.9.0, when their suites run, then `Money` imports and the import test passes.

## Implementation Notes

- 2026-11-05, mytp-core part done in `mytp-core-5.1` (`story/5.1-money`), uncommitted; version left at 0.7.0 (README, module docstrings and the compatibility rule already say 0.9.0). Files: new `mytp_core/money.py` (`Money`, `CurrencyMismatch`, `CURRENCIES`), new `tests/test_money.py` (143 tests, every matrix row), README module list + "Money" section + 0.9.0 compatibility line, `mytp_core/__init__.py` docstring, `contracts/amounts.py` docstring pointer, `pyproject.toml` description. Suite: 874 passed (SQLite).
- Choices beyond the plan's letter:
  - Absolute value must stay below 10**15 (`ValueError` at construction or on a result, e.g. a sum), so `to_contract()` can never fail on `Amount`'s 15-integer-digit limit.
  - Decimal strings are strict: `[+-]?digits[.digits]` only (no exponent, spaces, `_`, `.5`, `5.`) -> `ValueError`; other types (None, bytes, an `Amount`) -> `TypeError`.
  - Every Decimal operation uses an explicit module context (ROUND_HALF_UP, prec 40; products use a context sized to the operands so they are exact before the single rounding), so a site's thread decimal context never changes a result (tested with prec 3 / ROUND_DOWN).
  - `split(n)` with a non-int n (float, Decimal, bool) -> `TypeError`; n < 1 -> `ValueError`.
  - `Money` is picklable/copyable (`__reduce__`), since immutability blocks the default slot restore.
  - `from_contract` with an unknown currency (valid `Amount`, e.g. GBP) -> `ValueError` from `Money`; with a non-dict/non-Amount -> `TypeError`.
  - (Superseded by the review fixes below.) `==` across currencies raised, per the first plan.
- Review fixes (2026-11-05): `==` between different currencies is now False (`!=` True), per the user's decision; ordering and `+`/`-` across currencies still raise `CurrencyMismatch`, a bare number is still never equal, and the hash (amount, currency) stays consistent, so `in`, dicts and sets can mix currencies (README updated, the warning removed). The size pre-check compares the magnitude (`copy_abs() >= LIMIT`), so any zero passes (`0E+20`, `0.00 * 1E+100`). A second `__init__` on an instance raises `TypeError`, and writing the private slots with `object.__setattr__` is documented as outside the contract. The currency must be exactly `str` (a subclass with a permissive `__eq__` is refused) and the `CURRENCIES` constant is stored. `split(n)` refuses n > 10 000 (`MAX_PARTS`) with `ValueError`. The module docstring and README give the caller rule: build amounts and factors from strings or ints, never `Decimal(float)`. Each fix has a test; `tests/test_money.py`: 157 passed.
- Left for the coordinator: rebase onto 0.8.0, bump to 0.9.0 (`pyproject.toml`, `__init__.py`), tag, and the two site vendoring PRs with the import test.

## Plan Change Log

- 2026-11-05, before review triage: the frozen `==` rule changed by the user (equality across currencies is False; ordering and arithmetic still raise). KEEP everything else.

## Review Triage Log

Pass 1 (quick lens, opus, money-correctness probes, 2026-11-05): 6 findings (plus the `==` question raised by the implementer). medium 1, low 4, rejected 1.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 0 | `==` across currencies raises (breaks `in`, dict lookups) | medium | intent_gap → resolved | Frozen rule changed by the user (2026-11-05): `==` is False across currencies; ordering and arithmetic still raise. Patched with tests. |
| 1 | A zero with a large exponent is refused as too big (`0.00 * Decimal('1E+100')`) | medium | patch | Size check on the magnitude; any zero passes. |
| 2 | `Decimal(float)` accepted and rounded by its binary value | low | patch (docs) | Undetectable once the Decimal exists; the caller rule (strings or ints, never `Decimal(float)`) is in the docstring and README, pinned by a test. |
| 3 | `__init__` re-run on an instance changes it and its hash | low | patch | Re-initialisation refused; `object.__setattr__` documented as outside the contract. |
| 4 | A str subclass with a permissive `__eq__` passes the currency check | low | patch | Only `type(currency) is str`; the CURRENCIES constant is stored. |
| 5 | `split(n)` has no upper bound | low | patch | `MAX_PARTS = 10_000`. |
| 6 | int subclasses (IntEnum) accepted as amounts/factors | low | rejected | Harmless (exact integers); `bool` is refused; the fix would add type checks for no real risk. |

## Verification

**Commands:**
- mytp-core suite -- all pass; both sites' suites after vendoring -- all pass.
