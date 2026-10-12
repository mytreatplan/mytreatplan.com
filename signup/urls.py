from django.urls import path

from . import views

# Not namespaced: the nav and LOGIN_URL use the plain name `signup`.
urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('signup/<slug:slug>/', views.signup, name='signup_step'),
]
