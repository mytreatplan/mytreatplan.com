from django.apps import AppConfig


class PatientsConfig(AppConfig):
    """Patients and work orders (epic-ordering 9.1, AD-5, AD-15, AD-21): the regional
    patient record with its code, the work order of a paid case and its stage machine
    (the rules live in mytp-core `stages`), and the doctor's patients page."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'patients'
    verbose_name = 'Patients'

    def ready(self):
        from invoices import patients as invoice_patients
        from payments import hooks

        from . import service
        hooks.register('paid', 'work_orders', service.create_work_orders_for_order)
        invoice_patients.register(service.names_for_invoice)
