from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.messages import constants
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from .decorators import perm_criar_cobrancas, perm_editar_cobrancas, perm_ver_cobrancas
from .forms import ContratoForm, GerarCobrancasContratoForm
from .models import Contrato
from .services.contrato_cobrancas import gerar_cobrancas_do_contrato
from .services.contrato_crud import atualizar_contrato, contratos_queryset, criar_contrato
from .tenancy_write import (
    MSG_PARENT_TENANT,
    organization_for_finance_write,
    parent_in_organization,
    preserve_organization,
    reject_or_organization,
    update_allowed,
)


def _billing_organization(request):
    return organization_for_finance_write(request)


def _billing_org_or_404(request):
    org = _billing_organization(request)
    if org is None:
        raise Http404()
    return org


def _get_contrato(request, pk):
    org = _billing_org_or_404(request)
    return get_object_or_404(
        Contrato.objects.select_related("cliente", "responsavel", "criado_por"),
        pk=pk,
        organization=org,
    )


@perm_ver_cobrancas
@login_required
def contrato_listar(request):
    org = _billing_organization(request)
    contratos = contratos_queryset(org)
    cliente_id = (request.GET.get("cliente") or "").strip()
    if cliente_id.isdigit():
        contratos = contratos.filter(cliente_id=int(cliente_id))
    return render(
        request,
        "financeiro/contratos_listar.html",
        {"contratos": contratos, "cliente_id": cliente_id if cliente_id.isdigit() else ""},
    )


@perm_criar_cobrancas
@login_required
def contrato_novo(request):
    initial = {}
    cliente_id = (request.GET.get("cliente") or "").strip()
    org_get = _billing_organization(request)
    if cliente_id.isdigit() and org_get is not None:
        from usuarios.models import Cliente

        if Cliente.objects.filter(pk=int(cliente_id), organization=org_get).exists():
            initial["cliente"] = int(cliente_id)

    if request.method == "POST":
        org, denied = reject_or_organization(request, "financeiro_contrato_listar")
        if denied:
            return denied
        form = ContratoForm(request.POST, usuario=request.user, organization=org)
        if form.is_valid():
            contrato = form.save(commit=False)
            contrato.criado_por = request.user
            criar_contrato(contrato, autor=request.user, organization=org)
            messages.add_message(
                request, constants.SUCCESS, "Contrato criado com sucesso."
            )
            return redirect("financeiro_contrato_detalhe", pk=contrato.pk)
    else:
        form = ContratoForm(usuario=request.user, organization=org_get, initial=initial)

    return render(
        request,
        "financeiro/contrato_form.html",
        {"form": form, "titulo": "Novo contrato"},
    )


@perm_ver_cobrancas
@login_required
def contrato_detalhe(request, pk):
    contrato = _get_contrato(request, pk)
    cobrancas = (
        contrato.cobrancas.filter(organization=contrato.organization)
        .select_related("cliente")
        .order_by("data_vencimento", "id")
    )
    pode_gerar = not cobrancas.exclude(status="canceled").exists()
    return render(
        request,
        "financeiro/contrato_detalhe.html",
        {
            "contrato": contrato,
            "cobrancas": cobrancas,
            "pode_gerar": pode_gerar,
        },
    )


@perm_editar_cobrancas
@login_required
def contrato_editar(request, pk):
    contrato = _get_contrato(request, pk)
    original_org_id = contrato.organization_id
    org_lookup = contrato.organization
    if request.method == "POST":
        org, denied = reject_or_organization(request, "financeiro_contrato_listar")
        if denied:
            return denied
        if not update_allowed(contrato, org):
            messages.add_message(request, constants.ERROR, MSG_PARENT_TENANT)
            return redirect("financeiro_contrato_detalhe", pk=contrato.pk)
        form = ContratoForm(
            request.POST, instance=contrato, usuario=request.user, organization=org
        )
        if form.is_valid():
            contrato = form.save(commit=False)
            preserve_organization(contrato, original_org_id)
            contrato.save()
            atualizar_contrato(contrato)
            messages.add_message(
                request, constants.SUCCESS, "Contrato atualizado com sucesso."
            )
            return redirect("financeiro_contrato_detalhe", pk=contrato.pk)
    else:
        form = ContratoForm(
            instance=contrato, usuario=request.user, organization=org_lookup
        )

    return render(
        request,
        "financeiro/contrato_form.html",
        {"form": form, "titulo": "Editar contrato", "contrato": contrato},
    )


@perm_criar_cobrancas
@login_required
def contrato_gerar_cobrancas(request, pk):
    contrato = _get_contrato(request, pk)
    if contrato.cobrancas.exclude(status="canceled").exists():
        messages.add_message(
            request,
            constants.ERROR,
            "Este contrato já possui cobranças vinculadas.",
        )
        return redirect("financeiro_contrato_detalhe", pk=contrato.pk)

    if request.method == "POST":
        org, denied = reject_or_organization(
            request, "financeiro_contrato_detalhe", pk=contrato.pk
        )
        if denied:
            return denied
        if not parent_in_organization(contrato, org):
            messages.add_message(request, constants.ERROR, MSG_PARENT_TENANT)
            return redirect("financeiro_contrato_detalhe", pk=contrato.pk)
        form = GerarCobrancasContratoForm(request.POST, contrato=contrato)
        if form.is_valid():
            cobrancas = gerar_cobrancas_do_contrato(
                contrato,
                autor=request.user,
                organization=org,
                tipo_lancamento=form.cleaned_data["tipo_lancamento"],
                primeiro_vencimento=form.cleaned_data["primeiro_vencimento"],
                categoria=form.cleaned_data["categoria"],
                descricao=form.cleaned_data.get("descricao", ""),
                num_parcelas=form.cleaned_data.get("num_parcelas"),
                periodicidade=form.cleaned_data.get("periodicidade") or "mensal",
                forma_prevista_pagamento=form.cleaned_data.get(
                    "forma_prevista_pagamento", ""
                ),
            )
            messages.add_message(
                request,
                constants.SUCCESS,
                f"{len(cobrancas)} cobrança(s) gerada(s) a partir do contrato.",
            )
            return redirect("financeiro_cobranca_detalhe", pk=cobrancas[0].pk)
    else:
        form = GerarCobrancasContratoForm(contrato=contrato)

    return render(
        request,
        "financeiro/contrato_gerar_cobrancas.html",
        {"form": form, "contrato": contrato},
    )
