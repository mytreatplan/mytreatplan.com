from django.urls import path

from . import views

# Not namespaced (the doctor nav links the plain name `patients`); under accounts.page_records'
# PORTAL_PREFIXES ('patients/'). The patient code is the only thing a URL carries.
urlpatterns = [
    path('patients/', views.patient_list, name='patients'),
    path('patients/new/', views.new_patient, name='patient_new'),
    path('patients/<str:code>/', views.patient_page, name='patient'),
    path('patients/<str:code>/comments/', views.patient_comments, name='patient_comments'),
    path('patients/<str:code>/status/', views.patient_status, name='patient_status'),
    path('patients/<str:code>/portrait/', views.patient_portrait, name='patient_portrait'),
    path('patients/<str:code>/portrait/delete/', views.portrait_delete,
         name='patient_portrait_delete'),
]
