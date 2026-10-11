"""Onboarding financeiro e acionamento explícito de subconta Asaas."""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.messages import constants
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from financeiro.decorators import perm_provisionar_asaas
from financeiro.forms import OrganizationFinancialOnboardingForm
from financeiro.models import OrganizationAsaasSubaccount, OrganizationFinancialOnboarding
from financeiro.services.asaas_client import urllib_transport
from financeiro.services.asaas_onboarding import (
    FIELD_LABELS,
    missing_onboarding_fields,
    pending_fields_message,
)
from financeiro.services.asaas_provision import (
    AsaasProvisionError,
    lookup_existing_subaccount,
    provision_organization_subaccount,
)
from financeiro.tenancy_write import reject_or_organization


def _status_row(organization):
    return OrganizationAsaasSubaccount.objects.filter(organization=organization).first()


def _activation_status_label(*, instance, missing, subaccount) -> str:
    if subaccount is not None:
        if subaccount.status == OrganizationAsaasSubaccount.Status.CONFIGURED:
            return "Conta configurada"
        if subaccount.status == OrganizationAsaasSubaccount.Status.PROVISIONING:
            return "Em processamento"
        if subaccount.status in {
            OrganizationAsaasSubaccount.Status.ERROR,
            OrganizationAsaasSubaccount.Status.RECOVERY_REQUIRED,
        }:
            return "Requer atenção"
    if not missing:
        return "Pronto para ativação"
    if instance is not None and instance.pk:
        return "Cadastro salvo"
    return "Cadastro não concluído"


def _onboarding_checklist(*, missing, person_type: str) -> list[dict]:
    pending = set(missing)

    def group_done(keys: tuple[str, ...]) -> bool:
        return not any(FIELD_LABELS[key] in pending for key in keys)

    fiscais: tuple[str, ...] = ("income_value",)
    if person_type == OrganizationFinancialOnboarding.PersonType.FISICA:
        fiscais = ("income_value", "birth_date")
    elif person_type == OrganizationFinancialOnboarding.PersonType.JURIDICA:
        fiscais = ("income_value", "company_type", "tax_regime")
    return [
        {
            "label": "Dados cadastrais",
            "done": group_done(("legal_name", "email", "cpf_cnpj", "person_type")),
        },
        {"label": "Dados fiscais", "done": group_done(fiscais)},
        {
            "label": "Endereço",
            "done": group_done(
                ("address", "address_number", "province", "postal_code")
            ),
        },
        {"label": "Dados de contato", "done": group_done(("mobile_phone",))},
        {"label": "Pronto para ativação", "done": not missing},
    ]


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
            return redirect("financeiro_conta_onboarding")
    else:
        form = OrganizationFinancialOnboardingForm(instance=instance)
    subaccount = _status_row(org)
    missing = missing_onboarding_fields(instance)
    person_type = ""
    if instance is not None:
        person_type = str(instance.person_type or "")
    return render(
        request,
        "financeiro/asaas_onboarding.html",
        {
            "form": form,
            "subaccount": subaccount,
            "missing_fields": missing,
            "pending_fields_message": pending_fields_message(missing) if missing else "",
            "can_provision": not missing,
            "activation_status": _activation_status_label(
                instance=instance, missing=missing, subaccount=subaccount
            ),
            "onboarding_checklist": _onboarding_checklist(
                missing=missing, person_type=person_type
            ),
            "is_sandbox": (
                str(getattr(settings, "ASAAS_ENVIRONMENT", "") or "").strip().upper()
                == "SANDBOX"
            ),
        },
    )


@perm_provisionar_asaas
@login_required
@require_POST
def asaas_provision(request):
    org, denied = reject_or_organization(request, "financeiro_conta_onboarding")
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
                "A conta financeira desta organização já está configurada.",
            )
        else:
            messages.add_message(
                request,
                constants.SUCCESS,
                "Conta financeira ativada.",
            )
    except AsaasProvisionError as exc:
        if exc.reason == "missing_onboarding_fields":
            needed = pending_fields_message(list(exc.missing_fields))
            messages.add_message(request, constants.ERROR, needed)
        elif exc.reason == "already_provisioning":
            messages.add_message(
                request,
                constants.ERROR,
                "Ativação já em andamento. Não foi enviada outra solicitação.",
            )
        elif exc.reason == "recovery_required":
            messages.add_message(
                request,
                constants.ERROR,
                "A conta financeira exige recuperação. A ativação não foi reenviada.",
            )
        elif exc.reason == "worker":
            messages.add_message(
                request,
                constants.ERROR,
                "Ativação da conta financeira indisponível neste processo.",
            )
        elif exc.reason == "forbidden":
            messages.add_message(
                request,
                constants.ERROR,
                "Sem permissão para ativar a conta financeira.",
            )
        elif exc.reason == "timeout":
            messages.add_message(
                request,
                constants.ERROR,
                "Tempo esgotado na ativação. Recuperação necessária; a solicitação não será repetida automaticamente.",
            )
        else:
            messages.add_message(
                request,
                constants.ERROR,
                "Não foi possível ativar a conta financeira.",
            )
    return redirect("financeiro_conta_onboarding")


@perm_provisionar_asaas
@login_required
@require_POST
def asaas_recovery_lookup(request):
    org, denied = reject_or_organization(request, "financeiro_conta_onboarding")
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
                "Consulta localizou a conta financeira. A chave de acesso não é reemitida nesta fase.",
            )
        else:
            messages.add_message(
                request,
                constants.INFO,
                "Consulta não localizou a conta financeira.",
            )
    except AsaasProvisionError:
        messages.add_message(
            request,
            constants.ERROR,
            "Não foi possível consultar a situação da conta financeira.",
        )
    return redirect("financeiro_conta_onboarding")
