from django.urls import path

from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from . import pages, views
from .wizard import URL_NAME, CaseWizard

# The case wizard's steps (9.16): `start-a-case/<patient code>/<step>/`.
case_wizard = login_required(csrf_protect(never_cache(
    CaseWizard.as_view(url_name=URL_NAME, done_step_name='done'))))

# The product pages (9.5) keep the paths the home tiles always had.
# Not namespaced (the home and the doctor nav link the plain names `start_a_case` and
# `cart`); under accounts.page_records' PORTAL_PREFIXES ('start-a-case/', 'cart/', 'virtuaortho/', 'planning-services/'). A
# patient's code is the only thing a URL carries.
urlpatterns = [
    path('start-a-case/', views.start_a_case, name='start_a_case'),
    path('start-a-case/patient/', views.start_a_case_patient, name='start_a_case_patient'),
    path('start-a-case/<str:code>/', views.start_a_case_tps, name='start_a_case_tps'),
    path('start-a-case/<str:code>/<str:step>/', case_wizard, name=URL_NAME),
    path('virtuaortho/', pages.virtuaortho_page, name='virtuaortho'),
    path('planning-services/', pages.planning_services_page, name='planning_services'),
    path('cart/', views.cart, name='cart'),
    path('cart/finish/', views.cart_finish, name='cart_finish'),
    path('cart/<uuid:item_id>/remove/', views.cart_remove, name='cart_remove'),
]
