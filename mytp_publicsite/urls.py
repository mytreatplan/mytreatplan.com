from django.urls import path

from . import views

app_name = 'publicsite'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('privacy/', views.PrivacyView.as_view(), name='privacy'),
    path('terms/', views.TermsView.as_view(), name='terms'),
]
