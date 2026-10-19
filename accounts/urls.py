from django.urls import path

from . import views

# Not namespaced: the nav and LOGIN_URL use the plain names `login` and `logout`.
urlpatterns = [
    path('login/', views.login, name='login'),
    # The second factor: the choice of contact, then the code (accounts.views).
    path('login/verify/', views.login_verify, name='login_verify'),
    path('login/code/', views.login_code, name='login_code'),
    path('logout/', views.logout, name='logout'),
    path('welcome/', views.welcome, name='welcome'),
]
