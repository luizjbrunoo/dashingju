"""Views de listagem e seleção de contas Google Ads. Sem Mutate."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.messages import constants
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from financeiro.tenancy_write import MSG_TENANT_INDETERMINADO, organization_for_finance_write
from marketing.decorators import login_e_perm_manage_integracoes
from marketing.models import OrganizationGoogleAdsConnection
from marketing.services import google_ads_accounts as ads_accounts
from marketing.services.google_ads_accounts import (
    MSG_NOT_READY,
    MSG_SELECT_OK,
    GoogleAdsAccountsError,
)

_READY = frozenset(
    {
        OrganizationGoogleAdsConnection.Status.AUTHORIZED,
        OrganizationGoogleAdsConnection.Status.CONFIGURED,
    }
)


def _organization_or_redirect(request):
    organization = organization_for_finance_write(request)
    if organization is None:
        messages.add_message(request, constants.ERROR, MSG_TENANT_INDETERMINADO)
        return None, redirect("marketing_dashboard")
    return organization, None


def _connection_ready(organization):
    return (
        OrganizationGoogleAdsConnection.objects.filter(organization=organization)
        .first()
    )


@require_GET
@login_e_perm_manage_integracoes
def accounts(request):
    organization, bounced = _organization_or_redirect(request)
    if bounced:
        return bounced
    conn = _connection_ready(organization)
    if conn is None or conn.status not in _READY:
        messages.add_message(request, constants.ERROR, MSG_NOT_READY)
        return redirect("marketing_dashboard")
    contas = []
    try:
        contas = ads_accounts.list_accessible_accounts(organization=organization)
    except GoogleAdsAccountsError as exc:
        messages.add_message(request, constants.ERROR, exc.user_message)
        if exc.reason != "cloud_project_not_approved":
            return redirect("marketing_dashboard")
    return render(
        request,
        "marketing/google_ads_accounts.html",
        {
            "contas": contas,
            "google_ads_connection": conn,
            "subnav_section": "google_ads",
        },
    )


@require_POST
@login_e_perm_manage_integracoes
def select_account(request):
    organization, bounced = _organization_or_redirect(request)
    if bounced:
        return bounced
    conn = _connection_ready(organization)
    if conn is None or conn.status not in _READY:
        messages.add_message(request, constants.ERROR, MSG_NOT_READY)
        return redirect("marketing_dashboard")
    try:
        ads_accounts.select_google_ads_account(
            organization=organization,
            customer_id=request.POST.get("customer_id"),
            login_customer_id=request.POST.get("login_customer_id"),
        )
    except GoogleAdsAccountsError as exc:
        messages.add_message(request, constants.ERROR, exc.user_message)
        if exc.reason == "cloud_project_not_approved":
            return redirect("marketing_google_ads_accounts")
        return redirect("marketing_dashboard")
    messages.add_message(request, constants.SUCCESS, MSG_SELECT_OK)
    return redirect("marketing_dashboard")
