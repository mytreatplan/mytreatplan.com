from django.urls import path

from . import views

# Not namespaced (the home and the doctor nav link the plain names `start_a_case` and
# `cart`); under accounts.page_records' PORTAL_PREFIXES ('start-a-case/', 'cart/'). A
# patient's code is the only thing a URL carries.
urlpatterns = [
    path('start-a-case/', views.start_a_case, name='start_a_case'),
    path('start-a-case/patient/', views.start_a_case_patient, name='start_a_case_patient'),
    path('start-a-case/<str:code>/', views.start_a_case_tps, name='start_a_case_tps'),
    path('cart/', views.cart, name='cart'),
    path('cart/<uuid:item_id>/remove/', views.cart_remove, name='cart_remove'),
]
