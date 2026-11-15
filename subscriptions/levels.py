"""A subscription level's name as the doctor reads it (epic-subscriptions entries 2 and 3):
the upgrade screen (`subscriptions.views`) and the upgrade reminder of the doctor header
and home (`subscriptions.templatetags.upgrade_reminder`) name a level the same way."""
from django.utils.translation import pgettext_lazy

# A level's name when its catalogue row has none in any language.
TIER_NAMES = {
    'basic': pgettext_lazy('subscription level', 'Basic'),
    'advanced': pgettext_lazy('subscription level', 'Advanced'),
    'premium': pgettext_lazy('subscription level', 'Premium'),
}


def level_name(row, tier, language):
    """The catalogue row's public name in `language` (the row may be None), else the
    translated tier name."""
    return (row.name_in(language) if row is not None else '') or TIER_NAMES[tier]
