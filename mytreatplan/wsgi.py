"""
WSGI config for mytreatplan project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mytreatplan.settings')

application = get_wsgi_application()

# Refuse to start without valid field keys (system checks do not run under Gunicorn):
# a missing or malformed key raises here, naming the setting, instead of serving pages
# that all fail.
from accounts.crypto import validate_field_keys  # noqa: E402

validate_field_keys()
