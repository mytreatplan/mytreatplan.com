"""Read-only admin for the event log: an attempt with its events in order, data decrypted.
Nothing can be added, changed or deleted here."""
import json

from django.contrib import admin
from django.utils.html import format_html

from .models import Attempt, Event


class ReadOnlyMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class EventInline(ReadOnlyMixin, admin.TabularInline):
    model = Event
    fields = ('created_at', 'step', 'event', 'field', 'reason', 'data_display')
    readonly_fields = fields
    ordering = ('created_at', 'id')
    extra = 0
    can_delete = False

    @admin.display(description='data')
    def data_display(self, obj):
        if obj.data is None:
            return '-'
        return format_html('<pre style="margin:0">{}</pre>',
                           json.dumps(obj.data, indent=2, sort_keys=True, ensure_ascii=False))


@admin.register(Attempt)
class AttemptAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ('id', 'kind', 'account_id', 'started_at', 'ended_at', 'outcome')
    list_filter = ('kind', 'outcome')
    fields = ('id', 'kind', 'account_id', 'started_at', 'ended_at', 'outcome')
    readonly_fields = fields
    ordering = ('-started_at',)
    inlines = [EventInline]
