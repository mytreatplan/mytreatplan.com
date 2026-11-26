"""What the cart's views, wizard and form share: the doctor-only guard, the own-patient
lookup, the fiscal-entity block link and the texts of a refused cart action. Kept apart from
`cart.views` so `cart.forms`, `cart.wizard` and `cart.pages` import it without a cycle."""
from urllib.parse import quote

from django.http import Http404
from django.urls import reverse
from django.utils.translation import gettext as _

from accounts import fiscal
from accounts.client_sync import is_client
from patients import service as patients_service

from . import service


def refusal_texts():
    return {
        service.NOT_A_DOCTOR: _('Only doctors can order.'),
        service.NO_PATIENT: _('This patient cannot be chosen.'),
        service.NO_ENTITY: _('Add a fiscal entity before you order.'),
        service.MAKER_UNKNOWN: _('Choose a maker, "Not decided yet" or "Other".'),
        service.MAKER_LOGIN: _('Save your login for this maker first.'),
        service.OTHER_NAME: _('Enter the name of the maker (at most 60 characters).'),
        service.LIVE_WORK_ORDER: _('This patient already has a case in progress.'),
        service.IN_CART: _('This patient already has a service in your cart.'),
        service.NOT_OFFERED: _('This service is not available in that size.'),
        service.ADD_ON: _('One of the options chosen is not available for this service.'),
        service.NO_ADDRESS: _('Add a shipping address before you order.'),
        service.ADDRESS: _('Choose one of your shipping addresses.'),
        service.QUANTITY: _('Choose a number of aligners within the range offered.'),
        service.NOT_FINISHED: _('Retainers are for a patient whose treatment is finished.'),
        service.EMPTY: _('Your cart is empty.'),
        service.ADDRESS_GONE: _('The shipping address of one item is no longer available. '
                                'Remove that item and add it again.'),
        service.PAYMENT_OPEN: _('A payment for your earlier order is in progress. Please wait '
                                'a few minutes, or check that payment before you finish '
                                'again.'),
        service.ICE_TAKEN: _('The ICE credit of a patient in your cart is held by another '
                             'of your orders. If you finish again, this order is priced '
                             'without it.'),
        service.WORK_ORDER: _('This service can no longer be added to your cart.'),
    }


def doctor_only(request):
    if not is_client(request.user) or request.user.is_erased:
        raise Http404('not a doctor')


def own_patient(request, code):
    doctor_only(request)
    patient = patients_service.patient_of(request.user, code)
    if patient is None:
        raise Http404
    return patient


def fiscal_block(request, here):
    """The link of the fiscal-entity block (the wizard page to come back to), or None when
    the doctor has a usable entity."""
    if fiscal.has_usable_entity(request.user):
        return None
    return reverse('settings_fiscal_add') + '?next=' + quote(here, safe='')
