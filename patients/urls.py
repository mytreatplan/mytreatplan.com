from django.urls import path

from . import views

# Not namespaced (the doctor nav links the plain name `patients`); under accounts.page_records'
# PORTAL_PREFIXES ('patients/').
urlpatterns = [
    path('patients/', views.patient_list, name='patients'),
]
