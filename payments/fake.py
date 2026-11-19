"""The fake gateway, for development and staging (epic-payments entry 1, user decision
2026-11-18). Registered, but available only while PAYMENTS_FAKE_GATEWAY is True (default
off); the check payments.E001 refuses it on a public site. Its "hosted page" is a portal
page (payments.views.fake_gateway) with Pay and Fail buttons."""
from django.conf import settings
from django.urls import reverse

from . import gateways
from .checks import public_site


def enabled():
    return bool(getattr(settings, 'PAYMENTS_FAKE_GATEWAY', False)) and not public_site()


def start(order, request):
    return reverse('checkout_fake', args=[order.pk])


def register():
    gateways.register(gateways.FAKE, start, enabled=enabled)
