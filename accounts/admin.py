from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.utils.translation import gettext_lazy as _

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


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeAdminForm
    add_form = UserCreationAdminForm
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name')}),
        (_('Permissions'), {'fields': ('is_active', 'is_staff', 'is_superuser',
                                       'groups', 'user_permissions')}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'usable_password', 'password1', 'password2'),
        }),
    )
    list_display = ('email', 'first_name', 'last_name', 'is_staff')
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
        return queryset.filter(email_index=email_index(search_term)), False

    def log_addition(self, request, obj, message):
        return super().log_addition(request, _LoggedUser(obj), message)

    def log_change(self, request, obj, message):
        return super().log_change(request, _LoggedUser(obj), message)

    def log_deletions(self, request, queryset):
        return super().log_deletions(request, [_LoggedUser(user) for user in queryset])
