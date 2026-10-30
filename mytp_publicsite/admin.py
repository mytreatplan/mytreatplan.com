from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    """The contact form's messages: a read-only list and detail, decrypted, plus delete.
    The fields are encrypted, so there is no search; the mail's outcome can be filtered,
    to find the messages whose mail did not go out."""

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
