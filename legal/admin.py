"""Admin for the legal documents. A new version is added here (its number is set
automatically: the previous version of its kind + 1) and applies as soon as its
`published_at` passes, with no deploy. A published version is read-only and cannot be
deleted (as eventlog.admin, per object); a change means a new version. Choices are
evidence: read-only."""
from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from .models import LegalChoice, LegalDocument
from .rendering import render_markdown


@admin.register(LegalDocument)
class LegalDocumentAdmin(admin.ModelAdmin):
    list_display = ('kind', 'version', 'title_en', 'published_at', 'status')
    list_filter = ('kind',)
    ordering = ('kind', '-version')
    fields = ('kind', 'version', 'title_en', 'title_es', 'body_en', 'body_es', 'published_at',
              'preview_en', 'preview_es', 'created_at')
    readonly_fields = ('version', 'preview_en', 'preview_es', 'created_at')
    # No bulk actions: the bulk delete would bypass the per-object rule.
    actions = None

    def _is_published(self, obj):
        return obj is not None and obj.pk is not None and obj.is_published()

    def has_change_permission(self, request, obj=None):
        if self._is_published(obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if self._is_published(obj):
            return False
        return super().has_delete_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.pk is not None:
            # The kind of a saved version never changes (its number belongs to it).
            return ('kind', *self.readonly_fields)
        return self.readonly_fields

    @admin.display(description='status')
    def status(self, obj):
        now = timezone.now()
        if not obj.is_published(now):
            return 'draft'
        current = LegalDocument.objects.current(obj.kind, now)
        return 'current' if current and current.pk == obj.pk else 'superseded'

    @admin.display(description='preview (English)')
    def preview_en(self, obj):
        return self._preview(obj.body_en if obj else '')

    @admin.display(description='preview (Spanish)')
    def preview_es(self, obj):
        return self._preview(obj.body_es if obj else '')

    def _preview(self, body):
        if not body:
            return '-'
        return format_html('<div style="max-width:60em">{}</div>', render_markdown(body))


@admin.register(LegalChoice)
class LegalChoiceAdmin(admin.ModelAdmin):
    list_display = ('document', 'accepted', 'decided_at', 'user', 'pending_registration')
    list_filter = ('document__kind', 'accepted')
    fields = ('document', 'accepted', 'decided_at', 'user', 'pending_registration')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
