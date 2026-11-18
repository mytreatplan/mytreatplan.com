from django.urls import path

from . import views

# Not namespaced, as accounts.urls. Under accounts.page_records' PORTAL_PREFIXES
# ('checkout/'). The doctor sees only their own orders (any other id is a 404).
urlpatterns = [
    path('checkout/test-order/', views.test_order, name='checkout_test_order'),
    path('checkout/<uuid:pk>/', views.checkout, name='checkout'),
    path('checkout/<uuid:pk>/return/', views.checkout_return, name='checkout_return'),
    path('checkout/<uuid:pk>/failed/', views.checkout_failure, name='checkout_failure'),
    path('checkout/<uuid:pk>/fake/', views.fake_gateway, name='checkout_fake'),
]
