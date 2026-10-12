from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def welcome(request):
    """Landing page right after signup. Its content comes with the onboarding spec."""
    return render(request, 'accounts/welcome.html')
