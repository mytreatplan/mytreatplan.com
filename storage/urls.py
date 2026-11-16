"""Included by mytreatplan.urls: the upload endpoints at /storage/ (outside the language
prefixes)."""
from django.urls import path

from . import views

app_name = 'storage'

urlpatterns = [
    path('sign/', views.sign, name='sign'),
    path('complete/', views.complete, name='complete'),
]
