"""`{{ price|money }}`: a `Money` (mytp-core) as the page's language writes the number,
grouped, with its two decimals, then the currency code: "1,200.00 EUR" in English,
"1 200,00 EUR" in Spanish (Django's Spanish format groups with a no-break space) (epic-subscriptions entry 2, user decision 2026-11-14). The
amount is shown as it is, never computed here (AD-16). None, or anything that is not a
`Money` (no `amount` and `currency`), is ''."""
from django import template
from django.utils.formats import number_format

register = template.Library()


@register.filter
def money(value):
    amount = getattr(value, 'amount', None)
    currency = getattr(value, 'currency', None)
    if amount is None or not currency:
        return ''
    return f'{number_format(amount, decimal_pos=2, force_grouping=True)} {currency}'
