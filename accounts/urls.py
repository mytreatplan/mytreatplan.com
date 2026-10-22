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
