"""The VirtuaOrtho and Planning Services product pages (epic-ordering 9.5): what the doctor's
subscription offers, at what price, before starting a case. Doctor-only like the wizard (a
staff account is a 404). Products come only from `offers_for` through the `cart.service`
wrappers (the VirtuaOrtho rows through the wizard's own rule, `service.virtuaortho_lines`);
add-ons only from `resolver.add_ons`. Nothing is ordered here: "Start a case" opens the
wizard's step 1."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from catalogue import resolver
from catalogue.models import Product

from . import service
from .common import doctor_only
from .views import size_label


def _row(product, price, label=None, *, per_unit=False, note='', includes_odb_dt=False):
    """One product line: its name and description in the page's language, the net price and
    the add-ons offered on it with their prices."""
    language = translation.get_language()
    add_ons = [{'name': add_on.public_name, 'amount': add_on.amount_money,
                'percent': add_on.percent}
               for add_on in resolver.add_ons(product) if add_on.code != service.ODB_DT]
    return {'name': label or product.public_name, 'description': product.description_in(language),
            'price': price, 'per_unit': per_unit, 'note': note,
            'includes_odb_dt': includes_odb_dt, 'add_ons': add_ons}


def virtuaortho_groups(doctor):
    """[{'label', 'sections': [{'title', 'rows'}]}] one group per variant offered."""
    labels = {service.ONE: _('VirtuaOrtho One'), service.DIGITALTWIN: _('VirtuaOrtho DigitalTwin')}
    titles = {service.CASE: _('Sizes'), service.ODB: _('ODB'),
              service.SPARK: _('Spark on Demand'), service.RETAINERS: _('Retainers')}
    order = (service.CASE, service.ODB, service.SPARK, service.RETAINERS)
    lines = service.virtuaortho_lines(doctor)
    groups = []
    for variant in service.VARIANTS:
        mine = [line for line in lines if line[0] == variant]
        if not mine:
            continue
        sections = []
        for choice in order:
            found = sorted((line for line in mine if line[1] == choice),
                           key=lambda line: (service.size_rank(line[2].size), line[2].code or '',
                                             str(line[2].pk)))
            if not found:
                continue
            rows = [_row(product, price,
                         size_label(product.size) if choice == service.CASE else None,
                         per_unit=choice == service.SPARK,
                         note=_('After a finished treatment') if choice == service.RETAINERS
                         else '',
                         includes_odb_dt=choice == service.ODB and variant == service.DIGITALTWIN)
                    for _v, _c, product, price in found]
            sections.append({'title': titles[choice], 'rows': rows})
        groups.append({'label': labels[variant], 'sections': sections})
    return groups


def planning_sections(doctor):
    """[{'title', 'rows'}]: the ICE, the branded TPS sizes and the in-house TPS sizes offered."""
    sections = []
    ice = service.offered_ice(doctor)
    if ice:
        sections.append({'title': _('Initial case evaluation'), 'rows': [_row(*ice)]})
    tps = service.offered_tps(doctor)
    for category, title in ((Product.TPS_BRANDED, _('Treatment planning by the maker')),
                            (Product.TPS_IN_HOUSE, _('In-house printing treatment planning'))):
        found = sorted((pair for pair in tps if pair[0].category == category),
                       key=lambda pair: (service.size_rank(pair[0].size), pair[0].code or ''))
        if found:
            sections.append({'title': title, 'rows': [
                _row(product, price, size_label(product.size)) for product, price in found]})
    return sections


@login_required
@never_cache
@require_http_methods(['GET', 'HEAD'])
def virtuaortho_page(request):
    doctor_only(request)
    return render(request, 'cart/virtuaortho.html', {'groups': virtuaortho_groups(request.user)})


@login_required
@never_cache
@require_http_methods(['GET', 'HEAD'])
def planning_services_page(request):
    doctor_only(request)
    return render(request, 'cart/planning_services.html',
                  {'sections': planning_sections(request.user)})
