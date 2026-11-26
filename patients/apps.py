from django.apps import AppConfig


class PatientsConfig(AppConfig):
    """Patients and work orders (epic-ordering 9.1, AD-5, AD-15, AD-21): the regional
    patient record with its code, the work orders of an order's patient-service lines (9.8:
    held while a bank transfer is awaited, live once paid) and their stage machine (the
    rules live in mytp-core `stages`), the doctor's patients page, and the ICE deduction
    (9.7: patients.ice)."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'patients'
    verbose_name = 'Patients'

    def ready(self):
        from invoices import patients as invoice_patients
        from payments import hooks

        from . import ice, portrait, service
        # One work order per patient-service line (epic-ordering 9.8): held in
        # `pending_payment` while a bank transfer is awaited, moved to (or created in)
        # `pending_assignment` when paid, cancelled with its order.
        hooks.register('transfer_approved', 'work_orders_hold',
                       service.hold_work_orders_for_transfer)
        hooks.register('paid', 'work_orders', service.create_work_orders_for_order)
        hooks.register('cancelled', 'work_orders_cancel', service.cancel_work_orders_for_order)
        hooks.register('expired', 'work_orders_cancel', service.cancel_work_orders_for_order)
        # An ICE deduction follows its planning order (epic-ordering 9.7), as a voucher
        # redemption does: reserved when created, consumed when paid, released when
        # cancelled or expired.
        hooks.register('created', 'ice_reserve', ice.reserve)
        hooks.register('paid', 'ice_consume', ice.consume)
        hooks.register('cancelled', 'ice_release', ice.release)
        hooks.register('expired', 'ice_release', ice.release)
        invoice_patients.register(service.names_for_invoice)
        # The patient portrait's storage purpose (epic-ordering 9.2).
        portrait.register()
