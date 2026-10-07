from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from comercial.decorators import login_e_perm_dashboard, login_e_perm_metas
from comercial.forms import InvestimentoMidiaForm, MetaComercialForm
from comercial.models import InvestimentoMidia
from comercial.services.advisor import criar_followups_propostas
from comercial.services.dashboard import contexto_dashboard, parse_mes
from comercial.services.goals import meta_do_ano, salvar_meta, ticket_medio_contratos
from comercial.services.metrics import periodo_do_mes
from comercial.services.recommendations import (
    criar_tarefa_da_recomendacao,
    marcar_resolvida,
)
from financeiro.tenancy_write import organization_for_finance_write


def _qs_dashboard(request) -> str:
    params = []
    mes = (request.POST.get("mes") or request.GET.get("mes") or "").strip()
    aba = (request.POST.get("aba") or request.GET.get("aba") or "").strip()
    if mes:
        params.append(f"mes={mes}")
    if aba:
        params.append(f"aba={aba}")
    return ("?" + "&".join(params)) if params else ""


def _redirect_dashboard(request):
    return redirect(reverse("comercial_dashboard") + _qs_dashboard(request))


def _periodo_request(request):
    ref = parse_mes(request.GET.get("mes") or request.POST.get("mes"))
    return periodo_do_mes(ref.year, ref.month, cortar_hoje=True)


@login_e_perm_dashboard
def dashboard(request):
    periodo = _periodo_request(request)
    ctx = contexto_dashboard(
        request.user,
        organization=organization_for_finance_write(request),
        periodo=periodo,
        get_params=request.GET,
    )
    return render(
        request,
        "comercial/dashboard.html",
        {
            "ctx": ctx,
            "periodo": periodo,
            "dias": periodo.dias,
        },
    )


@login_e_perm_metas
def meta_editar(request):
    ano = timezone.localdate().year
    ano_raw = (request.GET.get("ano") or request.POST.get("ano") or "").strip()
    if ano_raw.isdigit():
        ano = int(ano_raw)

    organization = organization_for_finance_write(request)
    instancia = meta_do_ano(request.user, ano, organization=organization)
    auto_ticket = ticket_medio_contratos(organization)

    if request.method == "POST":
        form = MetaComercialForm(
            request.POST,
            instance=instancia,
            user=request.user,
            organization=organization,
        )
        if form.is_valid():
            if organization is None:
                messages.error(
                    request,
                    "Não foi possível determinar o escritório ativo para esta operação.",
                )
                return redirect("comercial_dashboard")
            cleaned = form.cleaned_data
            usar_auto = cleaned.get("usar_ticket_automatico")
            ticket = None if usar_auto else cleaned.get("ticket_medio")
            if usar_auto and auto_ticket.valor:
                ticket = auto_ticket.valor
            salvar_meta(
                request.user,
                ator=request.user,
                ano=cleaned["ano"],
                meta_anual=cleaned["meta_anual"],
                meta_mensal=cleaned.get("meta_mensal"),
                ticket_medio=ticket,
                ticket_medio_manual=not usar_auto and bool(cleaned.get("ticket_medio")),
                vigencia_inicio=cleaned["vigencia_inicio"],
                vigencia_fim=cleaned["vigencia_fim"],
                observacoes=cleaned.get("observacoes") or "",
                organization=organization,
            )
            messages.success(request, "Meta comercial salva.")
            return redirect("comercial_dashboard")
    else:
        form = MetaComercialForm(
            instance=instancia, user=request.user, organization=organization
        )

    return render(
        request,
        "comercial/meta_form.html",
        {
            "form": form,
            "auto_ticket": auto_ticket,
            "ano": ano,
        },
    )


@login_e_perm_dashboard
@require_POST
def acao_resolver(request):
    chave = (request.POST.get("chave") or "").strip()
    cliente_raw = (request.POST.get("cliente_id") or "").strip()
    cliente_id = int(cliente_raw) if cliente_raw.isdigit() else None
    ok = marcar_resolvida(
        request.user,
        ator=request.user,
        chave=chave,
        cliente_id=cliente_id,
        organization=organization_for_finance_write(request),
    )
    if ok:
        messages.success(request, "Ação marcada como resolvida.")
    else:
        messages.error(request, "Não foi possível resolver esta ação.")
    return _redirect_dashboard(request)


@login_e_perm_dashboard
@require_POST
def acao_criar_tarefa(request):
    chave = (request.POST.get("chave") or "").strip()
    cliente_raw = (request.POST.get("cliente_id") or "").strip()
    cliente_id = int(cliente_raw) if cliente_raw.isdigit() else None
    titulo = (request.POST.get("titulo") or "").strip()
    motivo = (request.POST.get("motivo") or "").strip()
    org = organization_for_finance_write(request)
    tarefa = criar_tarefa_da_recomendacao(
        request.user,
        ator=request.user,
        chave=chave,
        cliente_id=cliente_id,
        titulo=titulo,
        motivo=motivo,
        organization=org,
    )
    if tarefa is None:
        messages.error(request, "Não foi possível criar a tarefa (cliente inválido).")
        return _redirect_dashboard(request)
    marcar_resolvida(
        request.user,
        ator=request.user,
        chave=chave,
        cliente_id=cliente_id,
        organization=organization_for_finance_write(request),
    )
    messages.success(request, "Tarefa criada na Agenda.")
    return redirect("agenda")


@login_e_perm_dashboard
@require_POST
def advisor_criar_followups(request):
    n = criar_followups_propostas(
        request.user,
        ator=request.user,
        organization=organization_for_finance_write(request),
    )
    if n:
        messages.success(request, f"{n} follow-up(s) criados na Agenda.")
    else:
        messages.info(request, "Nenhuma proposta pendente para criar follow-up.")
    return _redirect_dashboard(request)


@login_e_perm_metas
def investimento_listar(request):
    organization = organization_for_finance_write(request)
    if organization is None:
        itens = InvestimentoMidia.objects.none()
    else:
        itens = InvestimentoMidia.objects.filter(organization=organization)[:50]
    form = InvestimentoMidiaForm()

    if request.method == "POST":
        form = InvestimentoMidiaForm(request.POST)
        if form.is_valid():
            if organization is None:
                messages.error(request, "Não foi possível determinar o escritório ativo.")
                return redirect("comercial_investimento")
            obj = form.save(commit=False)
            obj.organization = organization
            obj.usuario = request.user
            obj.criado_por = request.user
            obj.full_clean()
            obj.save()
            messages.success(request, "Investimento de mídia registrado.")
            return redirect("comercial_investimento")

    return render(
        request,
        "comercial/investimento_form.html",
        {"form": form, "itens": itens},
    )
