from django.urls import path

from . import views

app_name = 'publicsite'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
]
