from django.urls import path

from . import views

# Not namespaced, as payments.urls. Under accounts.page_records' PORTAL_PREFIXES
# ('invoices/'). The doctor sees only their own invoices (any other id is a 404).
urlpatterns = [
    path('invoices/', views.invoice_list, name='invoices'),
    path('invoices/<uuid:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),
]
