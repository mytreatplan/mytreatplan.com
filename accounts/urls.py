from django.urls import path

from . import password_reset, views

# Not namespaced: the nav and LOGIN_URL use the plain names `login` and `logout`, and
# signup's recovery note the plain `password_reset`.
urlpatterns = [
    path('login/', views.login, name='login'),
    # The second factor: the choice of contact, then the code (accounts.views).
    path('login/verify/', views.login_verify, name='login_verify'),
    path('login/code/', views.login_code, name='login_code'),
    path('logout/', views.logout, name='logout'),
    path('welcome/', views.welcome, name='welcome'),
    # The doctor area (epic-doctor-profile entry 1): the home, where a login lands, and one
    # address per area not built yet, all on the "coming soon" page. An epic that builds
    # an area replaces its line here with its own view, keeping the URL name.
    path('home/', views.home, name='home'),
    # `patients/` is the patients app's (epic-ordering 9.1, patients.urls).
    path('support/', views.coming_soon, {'area': 'support'}, name='support'),
    path('messages/', views.coming_soon, {'area': 'messages'}, name='messages'),
    # The settings area (epic-doctor-profile entry 2): the personal-data page keeps the
    # name `settings`; the sections not built yet are "coming soon" inside the area.
    path('settings/', views.personal_data, name='settings'),
    # The code-confirmed contact changes (entry 3): the mobile and the contact email.
    path('settings/mobile/', views.settings_mobile, name='settings_mobile'),
    path('settings/mobile/code/', views.settings_mobile_code, name='settings_mobile_code'),
    path('settings/email/', views.settings_email, name='settings_email'),
    path('settings/email/code/', views.settings_email_code, name='settings_email_code'),
    # The profile photo (entry 4): its upload page (and the attach), its confirmed delete.
    path('settings/photo/', views.settings_photo, name='settings_photo'),
    path('settings/photo/delete/', views.settings_photo_delete, name='settings_photo_delete'),
    # The maker logins (entry 8): the list, the other makers' pages (before the named
    # maker's own), the named maker's save/replace and confirmed delete.
    path('settings/maker/', views.settings_maker, name='settings_maker'),
    path('settings/maker/other/add/', views.settings_maker_other_add,
         name='settings_maker_other_add'),
    path('settings/maker/other/<uuid:pk>/', views.settings_maker_other_replace,
         name='settings_maker_other_replace'),
    path('settings/maker/other/<uuid:pk>/delete/', views.settings_maker_other_delete,
         name='settings_maker_other_delete'),
    path('settings/maker/<slug:maker>/', views.settings_maker_save,
         name='settings_maker_save'),
    path('settings/maker/<slug:maker>/delete/', views.settings_maker_delete,
         name='settings_maker_delete'),
    path('settings/password/', views.settings_coming_soon, {'section': 'password'},
         name='settings_password'),
    # The fiscal entities (entry 5): the list, add, edit, make default, confirmed delete.
    path('settings/fiscal/', views.settings_fiscal, name='settings_fiscal'),
    path('settings/fiscal/add/', views.settings_fiscal_add, name='settings_fiscal_add'),
    path('settings/fiscal/<uuid:pk>/edit/', views.settings_fiscal_edit,
         name='settings_fiscal_edit'),
    path('settings/fiscal/<uuid:pk>/default/', views.settings_fiscal_default,
         name='settings_fiscal_default'),
    path('settings/fiscal/<uuid:pk>/delete/', views.settings_fiscal_delete,
         name='settings_fiscal_delete'),
    # The shipping addresses (entry 7): the same five pages.
    path('settings/shipping/', views.settings_shipping, name='settings_shipping'),
    path('settings/shipping/add/', views.settings_shipping_add,
         name='settings_shipping_add'),
    path('settings/shipping/<uuid:pk>/edit/', views.settings_shipping_edit,
         name='settings_shipping_edit'),
    path('settings/shipping/<uuid:pk>/default/', views.settings_shipping_default,
         name='settings_shipping_default'),
    path('settings/shipping/<uuid:pk>/delete/', views.settings_shipping_delete,
         name='settings_shipping_delete'),
    path('tutorials/', views.coming_soon, {'area': 'tutorials'}, name='tutorials'),
    path('virtuaortho/', views.coming_soon, {'area': 'virtuaortho'}, name='virtuaortho'),
    path('planning-services/', views.coming_soon, {'area': 'planning_services'},
         name='planning_services'),
    # The password reset (accounts.password_reset): the request, its one answer, the
    # emailed link's page (the uid and token come in its fragment, posted by its script;
    # no route takes them), and the new password (the link from the session). Their names
    # are accounts.sessions.RESET_PAGES.
    path('password-reset/', password_reset.RequestView.as_view(), name='password_reset'),
    path('password-reset/sent/', password_reset.SentView.as_view(),
         name='password_reset_done'),
    path('reset/', password_reset.ConfirmView.as_view(opening=True),
         name='password_reset_confirm'),
    path('password-reset/new/', password_reset.ConfirmView.as_view(),
         name='password_reset_new'),
]
