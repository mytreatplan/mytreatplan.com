from django.urls import path

from . import views

# Under the patient file's prefix (accounts.page_records PORTAL_PREFIXES: 'patients/'); the
# patient code is the only thing a URL carries.
urlpatterns = [
    path('patients/<str:code>/records/', views.attach, name='case_records_attach'),
    path('patients/<str:code>/records/complete/', views.complete,
         name='case_records_complete'),
]
