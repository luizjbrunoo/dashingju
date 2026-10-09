"""Views OAuth Google Ads. Sem GAQL e sem persistir access_token."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.messages import constants
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_POST

from financeiro.tenancy_write import MSG_TENANT_INDETERMINADO
from marketing.decorators import login_e_perm_manage_integracoes
from marketing.services.google_ads_oauth import (
    GoogleAdsOAuthDenied,
    GoogleAdsOAuthError,
    GoogleAdsOAuthStateError,
    MSG_DISCONNECT_OK,
    MSG_OAUTH_FALHOU,
    MSG_OAUTH_OK,
    complete_authorization,
    disconnect_organization,
    start_authorization_url,
)


def _falhou(request):
    messages.add_message(request, constants.ERROR, MSG_OAUTH_FALHOU)
    return redirect("marketing_dashboard")


@require_POST
@login_e_perm_manage_integracoes
def connect(request):
    try:
        return redirect(start_authorization_url(request))
    except GoogleAdsOAuthError as exc:
        if str(exc) == "tenant":
            messages.add_message(request, constants.ERROR, MSG_TENANT_INDETERMINADO)
        else:
            messages.add_message(request, constants.ERROR, MSG_OAUTH_FALHOU)
        return redirect("marketing_dashboard")


@require_GET
@login_e_perm_manage_integracoes
def callback(request):
    try:
        complete_authorization(request)
    except GoogleAdsOAuthDenied:
        return _falhou(request)
    except (GoogleAdsOAuthStateError, GoogleAdsOAuthError):
        return _falhou(request)
    messages.add_message(request, constants.SUCCESS, MSG_OAUTH_OK)
    return redirect("marketing_dashboard")


@require_POST
@login_e_perm_manage_integracoes
def disconnect(request):
    try:
        disconnect_organization(request)
    except GoogleAdsOAuthError:
        messages.add_message(request, constants.ERROR, MSG_TENANT_INDETERMINADO)
        return redirect("marketing_dashboard")
    messages.add_message(request, constants.SUCCESS, MSG_DISCONNECT_OK)
    return redirect("marketing_dashboard")
