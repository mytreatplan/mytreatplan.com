from django.contrib import admin
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from . import contact_sync
from .models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    """The contact form's messages: a read-only list and detail, decrypted, plus delete.
    The fields are encrypted, so there is no search; the mail's outcome can be filtered,
    to find the messages whose mail did not go out. A delete (one, or the bulk action)
    tells the admin site in the same transaction (`contact_message.deleted` for each
    message sent before; mytp_publicsite.contact_sync)."""

    list_display = ('sent_at', 'sender', 'country', 'mail_status')
    list_filter = ('mail_status',)
    fields = ('sent_at', 'first_name', 'middle_name', 'surname', 'country', 'phone', 'email',
              'aligners', 'club', 'decision', 'mail_status', 'uid')
    readonly_fields = fields
    date_hierarchy = 'sent_at'

    @admin.display(description=_('sender'))
    def sender(self, obj):
        return ' '.join(part for part in (obj.first_name, obj.middle_name, obj.surname) if part)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def delete_model(self, request, obj):
        with transaction.atomic():
            contact_sync.delete([obj])

    def delete_queryset(self, request, queryset):
        with transaction.atomic():
            contact_sync.delete(queryset)
