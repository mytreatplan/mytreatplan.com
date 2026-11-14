from django.urls import path

from . import views

# Not namespaced, as accounts.urls: `upgrade` (signup's redirect) and `upgrade_purchase`
# (the placeholder entry 5's purchase page replaces). Under accounts.page_records'
# PORTAL_PREFIXES ('upgrade/').
urlpatterns = [
    path('upgrade/', views.upgrade, name='upgrade'),
    path('upgrade/<slug:level>/', views.upgrade_purchase, name='upgrade_purchase'),
]
