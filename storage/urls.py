"""Included by mytreatplan.urls: the upload endpoints at /storage/ (outside the language
prefixes), and the staging sample page under /<lang>/storage/sample/ only with
STORAGE_SAMPLE_UPLOADS."""
from django.urls import path

from . import sample, views

app_name = 'storage'

urlpatterns = [
    path('sign/', views.sign, name='sign'),
    path('complete/', views.complete, name='complete'),
]

sample_urlpatterns = ([
    path('', sample.page, name='page'),
    path('<uuid:pk>/download/', sample.download, name='download'),
    path('<uuid:pk>/delete/', sample.delete, name='delete'),
], 'storage_sample')
