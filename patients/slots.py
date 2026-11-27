"""Named slots on the patient file that other apps fill (epic-case-lifecycle 10.4).

The file has two slots, `case_actions` and `appointments`. An app registers a template (and
optionally a function returning its context) for a slot; the file renders each registered
entry in registration order, and renders nothing for a slot nobody registered in.
"""
CASE_ACTIONS = 'case_actions'
APPOINTMENTS = 'appointments'
SLOTS = (CASE_ACTIONS, APPOINTMENTS)

_registry = {slot: {} for slot in SLOTS}


def register(slot, name, template, context=None):
    """Add (or replace, by `name`) an entry of `slot`: `template` is rendered with the
    dict `context(request, patient)` returns (no context when it is None)."""
    if slot not in _registry:
        raise ValueError(f'unknown slot: {slot!r}')
    _registry[slot][name] = (template, context)


def unregister(slot, name):
    """Remove an entry (a no-op when it is not registered)."""
    if slot not in _registry:
        raise ValueError(f'unknown slot: {slot!r}')
    _registry[slot].pop(name, None)


def render_context(slot, request, patient):
    """The entries of `slot` for the template: a list of dicts with `template` and the
    entry's `context` (always including `patient`), empty when none is registered."""
    entries = []
    for name, (template, context) in list(_registry[slot].items()):
        values = dict(context(request, patient)) if context is not None else {}
        values.setdefault('patient', patient)
        entries.append({'name': name, 'template': template, 'context': values})
    return entries
