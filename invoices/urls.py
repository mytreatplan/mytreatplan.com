from django.urls import path

from . import views

# Not namespaced, as payments.urls. Under accounts.page_records' PORTAL_PREFIXES
# ('invoices/'). The doctor sees only their own invoices (any other id is a 404).
urlpatterns = [
    path('invoices/', views.invoice_list, name='invoices'),
    path('invoices/<uuid:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),
    # A pending-payment document (epic-ordering 9.19): the doctor's own order waiting for
    # its bank transfer only.
    path('invoices/pending/<uuid:order_pk>/pdf/', views.invoice_pending_pdf,
         name='invoice_pending_pdf'),
]
