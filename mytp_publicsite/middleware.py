from django.conf import settings


class RobotsNoindexMiddleware:
    """Adds `X-Robots-Tag: noindex, nofollow` to every response when ROBOTS_NOINDEX is on.

    Staging sets ROBOTS_NOINDEX=True so search engines never index it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(settings, 'ROBOTS_NOINDEX', False):
            response['X-Robots-Tag'] = 'noindex, nofollow'
        return response
