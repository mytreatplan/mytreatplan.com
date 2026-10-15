from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _, pgettext_lazy

from . import erasure
from .crypto import email_index
from .models import User


def _check_email_free(form, email, exclude_pk=None):
    """Reject an email already used by another user, whatever its case (blind index)."""
    others = User.objects.by_email(email)
    if exclude_pk is not None:
        others = others.exclude(pk=exclude_pk)
    if email and others.exists():
        raise form.instance.unique_error_message(User, ['email'])


class UserCreationAdminForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ('email',)

    def clean_email(self):
        """Normalise the email and reject one that differs from an existing one only in case."""
        email = User.objects.normalize_email(self.cleaned_data.get('email'))
        _check_email_free(self, email)
        return email


class UserChangeAdminForm(UserChangeForm):
    class Meta:
        model = User
        fields = '__all__'
        field_classes = {}

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data.get('email'))
        _check_email_free(self, email, exclude_pk=self.instance.pk)
        return email


class _LoggedUser:
    """What the admin log (django_admin_log.object_repr) stores for a user: its id, never
    the email, which would otherwise sit there in plaintext."""

    def __init__(self, user):
        self.pk = user.pk
        self._meta = user._meta

    def __str__(self):
        return f'User {self.pk}'


class ErasureStatusFilter(admin.SimpleListFilter):
    """Active (not erased) users by default; erased ones, or all, on request."""

    title = pgettext_lazy('erasure status', 'status')
    parameter_name = 'status'
    DEFAULT = 'active'

    def lookups(self, request, model_admin):
        # A context of their own, so they don't change Django's "All" of other filters.
        return (('active', pgettext_lazy('erasure status', 'Active')),
                ('erased', pgettext_lazy('erasure status', 'Erased')),
                ('all', pgettext_lazy('erasure status', 'All')))

    def value(self):
        return super().value() or self.DEFAULT

    def choices(self, changelist):
        for lookup, title in self.lookup_choices:
            yield {
                'selected': self.value() == lookup,
                'query_string': changelist.get_query_string({self.parameter_name: lookup}),
                'display': title,
            }

    def queryset(self, request, queryset):
        if self.value() == 'erased':
            return queryset.filter(erased_at__isnull=False)
        if self.value() == 'all':
            return queryset
        return queryset.filter(erased_at__isnull=True)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeAdminForm
    add_form = UserCreationAdminForm
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Personal info'), {'fields': ('title', 'first_name', 'middle_name', 'last_name',
                                         'country')}),
        (_('Contact'), {'fields': ('phone',)}),
        (_('Signup'), {'fields': ('how_found', 'how_found_other')}),
        (_('Permissions'), {'fields': ('is_active', 'is_staff', 'is_superuser',
                                       'groups', 'user_permissions')}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
        (_('Erasure'), {'fields': ('erased_at', 'purge_after')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'usable_password', 'password1', 'password2'),
        }),
    )
    # The mobile is validated and stored in E.164 form by signup step 1 (signup.forms), so
    # it is shown here but not edited.
    readonly_fields = ('phone', 'erased_at', 'purge_after')
    list_display = ('email', 'first_name', 'last_name', 'country', 'is_staff')
    list_filter = (ErasureStatusFilter, *BaseUserAdmin.list_filter)
    actions = ['erase_accounts']
    # Email and names are encrypted: the database can neither sort nor search them.
    sortable_by = ('is_staff',)
    ordering = ('id',)
    search_fields = ('email',)
    search_help_text = _('Search by the exact email address.')

    def get_search_results(self, request, queryset, search_term):
        """Exact email only, through the blind index (whatever its case)."""
        search_term = search_term.strip()
        if not search_term:
            return queryset, False
        index = email_index(search_term)
        # An erased account is found by its real index too (evidence), under the
        # "Erased" or "All" status.
        return queryset.filter(Q(email_index=index) | Q(erased_email_index=index)), False

    # --- Erasure: no plain delete, erased accounts read-only, the erase action ----------

    def has_delete_permission(self, request, obj=None):
        """Users are never deleted here: they are erased (accounts.erasure), and the
        daily purge deletes them after retention."""
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_erased:
            return False
        return super().has_change_permission(request, obj)

    def has_erase_permission(self, request):
        return request.user.has_perm(f'{self.opts.app_label}.delete_{self.opts.model_name}')

    @admin.action(description=_('Erase account'), permissions=['erase'])
    def erase_accounts(self, request, queryset):
        """Erase the selected accounts after an explicit confirmation (counts only in
        the messages: never an email or a name)."""
        try:
            purge_date = erasure.purge_date()
        except ImproperlyConfigured:
            self.message_user(request, gettext(
                'Accounts cannot be erased: ACCOUNT_ERASURE_RETENTION_DAYS is not set to a '
                'positive number of days.'),
                messages.ERROR)
            return None
        selected = list(queryset.order_by('pk'))
        users = [u for u in selected if not u.is_erased and u.pk != request.user.pk]
        # The skips are told once: on the first pass (the confirmation page), or when
        # nothing is left to erase.
        first_pass = request.POST.get('post') != 'yes'
        if (first_pass or not users) and any(u.pk == request.user.pk for u in selected):
            self.message_user(request, gettext('Your own account was skipped.'),
                              messages.WARNING)
        already = sum(1 for u in selected if u.is_erased)
        if already and (first_pass or not users):
            self.message_user(request, gettext(
                'Accounts skipped because they are already erased: %(count)d.')
                % {'count': already}, messages.WARNING)
        if not users:
            self.message_user(request, gettext('No account to erase.'), messages.WARNING)
            return None

        confirm_error = None
        if request.POST.get('post') == 'yes':
            if request.POST.get('confirm') == 'yes':
                now = timezone.now()
                erased = sum(1 for user in users if erasure.erase_user(user, by=request.user,
                                                                         now=now))
                self.message_user(request, gettext('Accounts erased: %(count)d.')
                                  % {'count': erased}, messages.SUCCESS)
                return None
            confirm_error = gettext("Tick \"I understand this can't be undone\" to erase.")

        context = {
            **self.admin_site.each_context(request),
            'title': gettext('Erase account'),
            'opts': self.opts,
            'count': len(users),
            'pks': [user.pk for user in users],
            'purge_date': purge_date,
            'confirm_error': confirm_error,
            'action': 'erase_accounts',
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'media': self.media,
        }
        request.current_app = self.admin_site.name
        return TemplateResponse(request, 'admin/accounts/user/erase_confirmation.html',
                                context)

    def log_addition(self, request, obj, message):
        return super().log_addition(request, _LoggedUser(obj), message)

    def log_change(self, request, obj, message):
        return super().log_change(request, _LoggedUser(obj), message)

    def log_deletions(self, request, queryset):
        return super().log_deletions(request, [_LoggedUser(user) for user in queryset])
