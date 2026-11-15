"""Root URLconf for tests: the real one plus stand-in `signup`, `login`, `logout`,
`welcome` routes and the doctor area's (`home` and its "coming soon" areas), as the portal
epics add them. Lets the tests see the nav's portal links appear."""
from django.conf.urls.i18n import i18n_patterns
from django.http import HttpResponse
from django.urls import path

from mytreatplan.urls import urlpatterns as site_urlpatterns


def _stub(request):
    return HttpResponse('stub')


urlpatterns = list(site_urlpatterns) + i18n_patterns(
    path('signup/', _stub, name='signup'),
    path('login/', _stub, name='login'),
    path('logout/', _stub, name='logout'),
    path('welcome/', _stub, name='welcome'),
    path('home/', _stub, name='home'),
    path('patients/', _stub, name='patients'),
    path('support/', _stub, name='support'),
    path('messages/', _stub, name='messages'),
    path('settings/', _stub, name='settings'),
    path('settings/maker/', _stub, name='settings_maker'),
    path('settings/password/', _stub, name='settings_password'),
    path('settings/fiscal/', _stub, name='settings_fiscal'),
    path('settings/shipping/', _stub, name='settings_shipping'),
    path('settings/mobile/', _stub, name='settings_mobile'),
    path('settings/mobile/code/', _stub, name='settings_mobile_code'),
    path('settings/email/', _stub, name='settings_email'),
    path('settings/email/code/', _stub, name='settings_email_code'),
    path('cart/', _stub, name='cart'),
    path('tutorials/', _stub, name='tutorials'),
    path('virtuaortho/', _stub, name='virtuaortho'),
    path('planning-services/', _stub, name='planning_services'),
    path('start-a-case/', _stub, name='start_a_case'),
    prefix_default_language=True,
)
