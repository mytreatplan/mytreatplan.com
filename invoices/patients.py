"""The patient-name lookup of the invoice PDF (epic-payments entry 10, AD-7, AD-23).

An invoice line holds only a patient code. The PDF shows the patient's current name next to
it, asked of the app that owns patients (`patients`, epic-ordering 9.1, registers the lookup
in its AppConfig.ready; with none registered the PDF shows codes only):

    invoices.patients.register(lookup)

`lookup(user, codes)` gets the doctor (the signed-in `User`) and a list of patient codes and
returns `{code: name}`; a code it does not know (an erased patient) is left out. The names
are used for one render and then dropped: never stored, logged, cached or put in a URL.

A single slot, as the lookup has one owner. `names_for` never raises.
"""
import logging

logger = logging.getLogger(__name__)

_lookup = None


def register(lookup):
    """Register the lookup `fn(user, codes) -> {code: name}`; a second one is an error."""
    global _lookup
    if not callable(lookup):
        raise ValueError('the patient lookup is callable')
    if _lookup is not None:
        raise ValueError('a patient lookup is already registered')
    _lookup = lookup


def unregister():
    """Remove the lookup (for tests); none is fine."""
    global _lookup
    _lookup = None


def names_for(user, codes):
    """`{code: name}` for the codes the lookup knows; `{}` when none is registered, when
    there are no codes or when the lookup fails (only the error's class is logged)."""
    codes = sorted({code for code in codes if code})
    if _lookup is None or not codes:
        return {}
    try:
        found = _lookup(user, codes)
        return {code: str(name) for code, name in dict(found).items()
                if code in codes and name}
    except Exception as error:  # noqa: BLE001 - a lookup must never break the PDF
        logger.error('The patient-name lookup failed (%s)', type(error).__name__)
        return {}
