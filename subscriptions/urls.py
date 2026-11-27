from django.urls import path

from . import views

# Not namespaced, as accounts.urls: `upgrade` (signup's redirect) and `upgrade_purchase`
# (entry 5's purchase page). Under accounts.page_records'
# PORTAL_PREFIXES ('upgrade/').
urlpatterns = [
    path('upgrade/', views.upgrade, name='upgrade'),
    # Epic-subscriptions 8.6: turn auto-renew on or off (POST only); before `<level>`.
    path('upgrade/auto-renew/', views.upgrade_auto_renew, name='upgrade_auto_renew'),
    path('upgrade/<slug:level>/', views.upgrade_purchase, name='upgrade_purchase'),
]
