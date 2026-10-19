from django.urls import path

from . import views

# Not namespaced: the nav and LOGIN_URL use the plain names `login` and `logout`.
urlpatterns = [
    path('login/', views.login, name='login'),
    path('logout/', views.logout, name='logout'),
    path('welcome/', views.welcome, name='welcome'),
]
