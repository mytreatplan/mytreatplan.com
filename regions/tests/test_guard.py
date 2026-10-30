"""AD-13: region-specific values come from the profile; naming a region outside regions/
is not allowed. Every string literal exactly 'eu' or 'ae' (a comparison, a constant such
as `EU = 'eu'`, a dict key, a `case`) in the portal's Python files is flagged, except in
regions/, migrations and tests. Uppercase country codes ('AE') are not region names."""
import ast
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)
REGION_NAMES = frozenset({'eu', 'ae'})
# Not portal code, or allowed to name regions (the module itself, migrations, tests).
SKIPPED_DIRS = frozenset({
    'regions', 'migrations', 'tests', 'node_modules', 'vendor', 'static', 'static_root',
    '__pycache__', '.git', '.venv', 'venv', '_bmad', '_bmad-output',
})


def region_literals(source, filename='<source>'):
    """Line numbers of the string literals naming a region in `source`."""
    return sorted(
        node.lineno for node in ast.walk(ast.parse(source, filename))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value in REGION_NAMES
    )


def portal_files():
    for path in sorted(ROOT.rglob('*.py')):
        parts = path.relative_to(ROOT).parts
        if any(part in SKIPPED_DIRS or part.startswith('.') for part in parts[:-1]):
            continue
        if parts[-1].startswith('test_') or parts[-1] == 'conftest.py':
            continue
        yield path


def test_no_region_names_outside_the_profile_module():
    found = [
        f'{path.relative_to(ROOT)}:{line}'
        for path in portal_files()
        for line in region_literals(path.read_text(encoding='utf-8'), str(path))
    ]
    assert not found, ('Read the region profile (settings.REGION_PROFILE) instead of naming '
                       'a region: ' + ', '.join(found))


def test_the_guard_scans_the_portal():
    scanned = {str(path.relative_to(ROOT)) for path in portal_files()}
    assert str(Path('mytreatplan') / 'settings.py') in scanned
    assert str(Path('signup') / 'views.py') in scanned
    assert not [p for p in scanned if p.startswith('regions')]


def test_the_guard_finds_each_form():
    source = '\n'.join([
        "if settings.REGION == 'eu': pass",           # 1
        'if "ae" != region: pass',                    # 2
        "ok = region in ('eu', 'us-later')",          # 3
        "ok = region not in ['ae']",                  # 4
        "match region:",                              # 5
        "    case 'eu': pass",                        # 6
        "country = 'AE'; same = country == 'AE'",     # 7: an ISO country, not a region
        "EU = 'eu'",                                  # 8: a constant
        "prefix = {'eu': 'EU', 'ae': 'AE'}",          # 9: a dict keyed by region
        "ok = region == profile.code",                # 10
        "text = 'eur'; lang = 'es'",                  # 11: not exactly a region name
    ])
    assert region_literals(source) == [1, 2, 3, 4, 6, 8, 9, 9]
