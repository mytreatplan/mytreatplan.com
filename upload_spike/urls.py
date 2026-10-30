"""Included by mytreatplan.urls under /<lang>/upload-spike/ only with UPLOAD_SPIKE_ENABLED."""
from django.urls import path

from . import views

app_name = 'upload_spike'

urlpatterns = [
    path('', views.page, name='page'),
    path('sign/', views.sign, name='sign'),
    path('object/', views.stat, name='object'),
]
