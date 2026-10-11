"""Onboarding financeiro e acionamento explícito de subconta Asaas."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.messages import constants
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from financeiro.decorators import perm_provisionar_asaas
from financeiro.forms import OrganizationFinancialOnboardingForm
from financeiro.models import OrganizationAsaasSubaccount, OrganizationFinancialOnboarding
from financeiro.services.asaas_client import urllib_transport
from financeiro.services.asaas_onboarding import missing_onboarding_fields
from financeiro.services.asaas_provision import (
    AsaasProvisionError,
    lookup_existing_subaccount,
    provision_organization_subaccount,
)
from financeiro.tenancy_write import reject_or_organization


def _status_row(organization):
    return OrganizationAsaasSubaccount.objects.filter(organization=organization).first()


@perm_provisionar_asaas
@login_required
def asaas_onboarding(request):
    org, denied = reject_or_organization(request, "financeiro_dashboard")
    if denied:
        return denied
    instance = OrganizationFinancialOnboarding.objects.filter(organization=org).first()
    if request.method == "POST":
        form = OrganizationFinancialOnboardingForm(request.POST, instance=instance)
        if form.is_valid():
            row = form.save(commit=False)
            row.organization = org
            row.save()
            messages.add_message(
                request,
                constants.SUCCESS,
                "Dados financeiros da organização salvos.",
            )
            return redirect("financeiro_asaas_onboarding")
    else:
        form = OrganizationFinancialOnboardingForm(instance=instance)
    subaccount = _status_row(org)
    missing = missing_onboarding_fields(instance)
    return render(
        request,
        "financeiro/asaas_onboarding.html",
        {
            "form": form,
            "subaccount": subaccount,
            "missing_fields": missing,
            "can_provision": not missing,
        },
    )


@perm_provisionar_asaas
@login_required
@require_POST
def asaas_provision(request):
    org, denied = reject_or_organization(request, "financeiro_asaas_onboarding")
    if denied:
        return denied
    try:
        result = provision_organization_subaccount(
            organization=org,
            user=request.user,
            transport=urllib_transport,
        )
        if result.reason == "already_configured":
            messages.add_message(
                request,
                constants.INFO,
                "A subconta Asaas desta organização já está configurada.",
            )
        else:
            messages.add_message(
                request,
                constants.SUCCESS,
                "Subconta Asaas provisionada.",
            )
    except AsaasProvisionError as exc:
        if exc.reason == "missing_onboarding_fields":
            needed = ", ".join(exc.missing_fields) or "dados obrigatórios"
            messages.add_message(
                request,
                constants.ERROR,
                f"Preencha os dados financeiros antes de criar a subconta: {needed}.",
            )
        elif exc.reason == "already_provisioning":
            messages.add_message(
                request,
                constants.ERROR,
                "Provisionamento já em andamento. Não foi enviada outra criação.",
            )
        elif exc.reason == "recovery_required":
            messages.add_message(
                request,
                constants.ERROR,
                "A subconta exige recuperação manual. A criação não foi reenviada.",
            )
        elif exc.reason == "worker":
            messages.add_message(
                request,
                constants.ERROR,
                "Provisionamento Asaas indisponível neste processo.",
            )
        elif exc.reason == "forbidden":
            messages.add_message(
                request,
                constants.ERROR,
                "Sem permissão para provisionar a subconta Asaas.",
            )
        elif exc.reason == "timeout":
            messages.add_message(
                request,
                constants.ERROR,
                "Tempo esgotado na criação. Recuperação necessária; a criação não será repetida automaticamente.",
            )
        else:
            messages.add_message(
                request,
                constants.ERROR,
                "Não foi possível provisionar a subconta Asaas.",
            )
    return redirect("financeiro_asaas_onboarding")


@perm_provisionar_asaas
@login_required
@require_POST
def asaas_recovery_lookup(request):
    org, denied = reject_or_organization(request, "financeiro_asaas_onboarding")
    if denied:
        return denied
    try:
        match = lookup_existing_subaccount(
            organization=org,
            user=request.user,
            transport=urllib_transport,
        )
        if match:
            messages.add_message(
                request,
                constants.INFO,
                "Consulta à conta-pai localizou a subconta. A apiKey não é reemitida nesta fase.",
            )
        else:
            messages.add_message(
                request,
                constants.INFO,
                "Consulta à conta-pai não localizou a subconta.",
            )
    except AsaasProvisionError:
        messages.add_message(
            request,
            constants.ERROR,
            "Não foi possível consultar a subconta Asaas.",
        )
    return redirect("financeiro_asaas_onboarding")
