"""Routed at /api/v1/ by mytreatplan/urls.py, outside the language prefixes."""
from django.urls import path

from . import views

urlpatterns = [
    path('ping', views.ping, name='interface_ping'),
    path('config/nudge', views.config_nudge, name='interface_config_nudge'),
    path('clients/resync', views.clients_resync, name='interface_clients_resync'),
    path('contact-messages/resync', views.contact_messages_resync,
         name='interface_contact_messages_resync'),
    path('remote/sample-record', views.remote_sample_record,
         name='interface_remote_sample_record'),
]
