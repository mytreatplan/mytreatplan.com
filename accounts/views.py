"""The registered area's entry and exit: login, logout and the welcome page.

Login checks the CAPTCHA (signup.captcha) first, then the credentials (accounts.forms),
then completes through `complete_login`, the one place a login is finished (the second
factor goes there). Logout is a POST only.
"""
import logging

from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from eventlog.service import client_ip
from signup import captcha

from .forms import LoginForm

logger = logging.getLogger(__name__)

# Where a login lands without a safe `next`.
LOGIN_REDIRECT = 'welcome'


def requested_next(request):
    """The `next` of the POST or the query string when it is safe (this host, and HTTPS
    when the request is, as Django's LoginView checks it), else ''."""
    url = request.POST.get(REDIRECT_FIELD_NAME, request.GET.get(REDIRECT_FIELD_NAME, ''))
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return url
    return ''


def redirect_after_login(request):
    """To the safe `next` as given (a literal URL, never reversed as a view name, as
    Django's LoginView does), else to the welcome page."""
    return HttpResponseRedirect(requested_next(request) or resolve_url(LOGIN_REDIRECT))


def complete_login(request, user):
    """Finish the login of `user`, whose password was just checked: log them in and send
    them to the safe `next`, or the welcome page."""
    auth_login(request, user)
    return redirect_after_login(request)


@sensitive_post_parameters('password')
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def login(request):
    if request.user.is_authenticated:
        return redirect_after_login(request)
    if request.method != 'POST':
        form = LoginForm(request)
    else:
        form = LoginForm(request, data=request.POST)
        result = captcha.verify_turnstile(request.POST.get(captcha.TOKEN_FIELD, ''),
                                          client_ip(request))
        if result == captcha.FAILED:
            form.refuse('captcha_failed')
        else:
            if result == captcha.UNAVAILABLE:
                # The login goes on (decision 1: the login lockout and IP limits, entry 5,
                # are the guard during an outage). No personal value is logged.
                logger.warning('check_unavailable: step=login field=captcha reason=%s',
                               captcha.TURNSTILE_CHECK)
            if form.is_valid():
                return complete_login(request, form.get_user())
    return render(request, 'accounts/login.html', {
        'form': form,
        'next': requested_next(request),
        **captcha.widget_context(),
    })


@require_POST
@csrf_protect
@never_cache
def logout(request):
    auth_logout(request)
    messages.success(request, _('You have logged out.'))
    return redirect('login')


@login_required
def welcome(request):
    """Landing page right after signup or login. Its content comes with the onboarding spec."""
    return render(request, 'accounts/welcome.html')
