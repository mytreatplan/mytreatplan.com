from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.utils.translation import gettext_lazy as _

from .models import User


class UserCreationAdminForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ('email',)

    def clean_email(self):
        """Normalise the email and reject one that differs from an existing one only in case."""
        email = User.objects.normalize_email(self.cleaned_data.get('email'))
        if email and User.objects.filter(email__iexact=email).exists():
            raise self.instance.unique_error_message(User, ['email'])
        return email


class UserChangeAdminForm(UserChangeForm):
    class Meta:
        model = User
        fields = '__all__'
        field_classes = {}


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
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)
