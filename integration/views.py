"""The interface endpoints the admin site calls (epic-admin-interface, AD-4). Entry 1 has
only the ping; the contracts come with entry 2."""
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .calls import signed_endpoint


@require_GET
@signed_endpoint
def ping(request):
    """GET /api/v1/ping, signed by the admin site: this site's name, region and time."""
    return JsonResponse({'site': settings.SITE, 'region': settings.REGION,
                         'time': timezone.now().isoformat()})
