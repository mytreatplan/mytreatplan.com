"""The configuration snapshot's `country_map` section, applied (epic-catalogue entry 3,
AD-3, AD-14; registered with integration.config.register_section_applier by regions.apps;
run inside the snapshot apply's transaction, so a failure keeps the previous map).

- Section present: each listed country's row (regions.models.Country) is replaced by code
  with the listed state, created when missing. A country the section does not list keeps
  its row, and so does one whose state this release does not know (`STATE_CHOICES`):
  skipped and counted, as the contract says readers do.
- Section absent (an admin site before mytp-core 0.15.0): the rows are left as they are,
  so the seed (migration 0002) stays the map until the first section arrives. Unlike the
  catalogue, an absent section never empties the table: a portal without a map would read
  every country as the default region.

The summary for the applied log line holds counts only, never codes or states (none for
an absent section).
"""
from .models import STATE_CHOICES, Country

KNOWN_STATES = frozenset(code for code, _label in STATE_CHOICES)


def apply(section):
    """Apply `section` (mytp-core's CountryMapSection, or None). Returns the log line's
    summary."""
    if section is None:
        return None
    current = dict(Country.objects.values_list('code', 'state'))
    changed, created, skipped = [], [], 0
    for entry in section.countries:
        if entry.state not in KNOWN_STATES:
            skipped += 1
            continue
        if entry.code not in current:
            created.append(Country(code=entry.code, state=entry.state))
        elif current[entry.code] != entry.state:
            changed.append(Country(code=entry.code, state=entry.state))
    Country.objects.bulk_update(changed, ['state'])
    Country.objects.bulk_create(created)
    return (f'country map: {len(section.countries)} listed, {len(changed)} changed, '
            f'{len(created)} added, {skipped} skipped')
