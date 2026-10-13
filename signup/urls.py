from django.urls import path

from . import views

# Not namespaced: the nav and LOGIN_URL use the plain name `signup`.
urlpatterns = [
    path('signup/', views.signup, name='signup'),
    # Before signup/<slug>/, which would take "resume" or "check-email" as a step slug.
    path('signup/resume/', views.resume, name='signup_resume'),
    path('signup/check-email/', views.check_email, name='signup_check_email'),
    path('signup/<slug:slug>/', views.signup, name='signup_step'),
]
