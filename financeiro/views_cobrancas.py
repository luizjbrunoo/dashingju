from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.messages import constants
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from .decorators import (
    perm_cancelar_cobrancas,
    perm_criar_cobrancas,
    perm_editar_cobrancas,
    perm_registrar_recebimentos,
    perm_ver_cobrancas,
    perm_ver_relatorios,
)
from .permissions import pode_ver_recebimentos
from .forms import CobrancaForm, RecebimentoForm
from .choices import CategoriaCobranca, FormaPagamento, StatusCobranca, TomMensagemCobranca
from .models import Cobranca, CobrancaRecebimento
from .services.cobranca_crud import (
    atualizar_cobranca,
    cancelar_cobranca,
    criar_cobranca,
)
from .services.cobranca_inadimplencia import calcular_inadimplencia
from .services.cobranca_previsao import calcular_previsao
from .services.cobranca_listagem import (
    CobrancaFiltros,
    calcular_kpis_cobrancas_organization,
    cobrancas_para_listagem_organization,
    opcoes_filtro_clientes_organization,
    opcoes_filtro_responsaveis,
)
from .services.cobranca_agenda import (
    compromisso_lembrete_automatico,
    compromissos_vinculados_cobranca,
    criar_compromisso_cobranca,
    criar_tarefa_cobranca,
    tarefas_vinculadas_cobranca,
)
from .services.cobranca_parcelamento import criar_cobrancas_parceladas, parcelas_do_grupo
from .services.cobranca_mensagem import (
    CobrancaMensagemError,
    gerar_mensagem_ia,
    gerar_mensagem_padrao,
    ia_disponivel,
    montar_contexto_mensagem,
)
from .services.cobranca_recebimento import estornar_recebimento, registrar_recebimento
from .services.cobranca_auditoria import (
    historico_auditoria_organization,
    resumo_auditoria_organization,
)
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


def _get_cobranca(request, pk):
    org = _billing_org_or_404(request)
    return get_object_or_404(
        Cobranca.objects.select_related(
            "cliente", "responsavel", "criado_por", "contrato"
        ),
        pk=pk,
        organization=org,
    )


@perm_ver_cobrancas
@login_required
def cobranca_listar(request):
    filtros = CobrancaFiltros.from_request(request)
    org = _billing_organization(request)
    cobrancas = cobrancas_para_listagem_organization(org, filtros)
    return render(
        request,
        "financeiro/cobrancas_listar.html",
        {
            "cobrancas": cobrancas,
            "kpis": calcular_kpis_cobrancas_organization(org),
            "filtros": filtros,
            "status_choices": StatusCobranca.choices,
            "categoria_choices": CategoriaCobranca.choices,
            "forma_choices": FormaPagamento.choices,
            "clientes_filtro": opcoes_filtro_clientes_organization(org),
            "responsaveis_filtro": opcoes_filtro_responsaveis(request.user),
            "aba_cobrancas": "todas",
            "filtros_query": filtros.query_string(),
        },
    )


@perm_ver_relatorios
@login_required
def cobranca_inadimplencia(request):
    org = _billing_organization(request)
    resumo = calcular_inadimplencia(org)
    maior_faixa = max(resumo.faixas, key=lambda f: f.total, default=None)
    if maior_faixa and maior_faixa.total <= 0:
        maior_faixa = None
    return render(
        request,
        "financeiro/cobrancas_inadimplencia.html",
        {
            "resumo": resumo,
            "maior_faixa": maior_faixa,
            "aba_cobrancas": "inadimplencia",
            "filtros_query": "",
        },
    )


@perm_ver_relatorios
@login_required
def cobranca_previsao(request):
    org = _billing_organization(request)
    resumo = calcular_previsao(org)
    return render(
        request,
        "financeiro/cobrancas_previsao.html",
        {
            "resumo": resumo,
            "aba_cobrancas": "previsao",
            "filtros_query": "",
        },
    )


@perm_criar_cobrancas
@login_required
def cobranca_nova(request):
    initial = {}
    cliente_id = (request.GET.get("cliente") or "").strip()
    org_get = _billing_organization(request)
    if cliente_id.isdigit() and org_get is not None:
        from usuarios.models import Cliente

        if Cliente.objects.filter(pk=int(cliente_id), organization=org_get).exists():
            initial["cliente"] = int(cliente_id)

    if request.method == "POST":
        org, denied = reject_or_organization(request, "financeiro_cobranca_listar")
        if denied:
            return denied
        form = CobrancaForm(request.POST, usuario=request.user, organization=org)
        if form.is_valid():
            if form.cleaned_data.get("tipo_lancamento") == "parcelada":
                parcelas = criar_cobrancas_parceladas(
                    usuario=request.user,
                    autor=request.user,
                    cliente=form.cleaned_data["cliente"],
                    descricao=form.cleaned_data["descricao"],
                    valor_total=form.cleaned_data["valor"],
                    primeiro_vencimento=form.cleaned_data["data_vencimento"],
                    num_parcelas=form.cleaned_data["num_parcelas"],
                    periodicidade=form.cleaned_data["periodicidade"],
                    categoria=form.cleaned_data["categoria"],
                    organization=org,
                    responsavel=form.cleaned_data.get("responsavel"),
                    contrato=form.cleaned_data.get("contrato"),
                    forma_prevista_pagamento=form.cleaned_data.get(
                        "forma_prevista_pagamento", ""
                    ),
                    observacoes_internas=form.cleaned_data.get("observacoes_internas", ""),
                    salvar_como_rascunho=form.cleaned_data.get("salvar_como_rascunho"),
                )
                messages.add_message(
                    request,
                    constants.SUCCESS,
                    f"{len(parcelas)} cobranças parceladas criadas com sucesso.",
                )
                return redirect("financeiro_cobranca_detalhe", pk=parcelas[0].pk)
            cobranca = form.save(commit=False)
            cobranca.criado_por = request.user
            criar_cobranca(cobranca, autor=request.user, organization=org)
            messages.add_message(
                request, constants.SUCCESS, "Cobrança criada com sucesso."
            )
            return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)
    else:
        form = CobrancaForm(
            usuario=request.user, organization=org_get, initial=initial
        )

    return render(
        request,
        "financeiro/cobranca_form.html",
        {"form": form, "titulo": "Nova cobrança"},
    )


@perm_ver_cobrancas
@login_required
def cobranca_detalhe(request, pk):
    cobranca = _get_cobranca(request, pk)
    cobranca.atualizar_status(salvar=True)
    historico = cobranca.historico.select_related("autor").all()[:50]
    if pode_ver_recebimentos(request.user):
        recebimentos = cobranca.recebimentos.filter(
            cancelado_em__isnull=True,
            organization=cobranca.organization,
        ).select_related("registrado_por")
    else:
        recebimentos = CobrancaRecebimento.objects.none()
    pode_receber = (
        cobranca.status not in (StatusCobranca.CANCELED, StatusCobranca.DRAFT)
        and cobranca.saldo > 0
    )
    parcelas_grupo = parcelas_do_grupo(cobranca) if cobranca.grupo_parcelamento_id else []
    pode_agendar = cobranca.status not in (StatusCobranca.CANCELED, StatusCobranca.DRAFT) and cobranca.saldo > 0
    pode_cobrar = pode_agendar
    return render(
        request,
        "financeiro/cobranca_detalhe.html",
        {
            "cobranca": cobranca,
            "historico": historico,
            "recebimentos": recebimentos,
            "pode_receber": pode_receber,
            "pode_agendar": pode_agendar,
            "pode_cobrar": pode_cobrar,
            "parcelas_grupo": parcelas_grupo,
            "agenda_tarefas": tarefas_vinculadas_cobranca(cobranca),
            "agenda_compromissos": compromissos_vinculados_cobranca(cobranca),
            "compromisso_lembrete_auto": compromisso_lembrete_automatico(cobranca),
        },
    )


@perm_editar_cobrancas
@login_required
def cobranca_editar(request, pk):
    cobranca = _get_cobranca(request, pk)
    if cobranca.status == StatusCobranca.CANCELED:
        messages.add_message(
            request,
            constants.ERROR,
            "Cobranças canceladas não podem ser editadas.",
        )
        return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)

    original_org_id = cobranca.organization_id
    if request.method == "POST":
        org, denied = reject_or_organization(
            request, "financeiro_cobranca_detalhe", pk=cobranca.pk
        )
        if denied:
            return denied
        if not update_allowed(cobranca, org):
            messages.add_message(request, constants.ERROR, MSG_PARENT_TENANT)
            return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)
        vencimento_anterior = cobranca.data_vencimento
        responsavel_anterior_id = cobranca.responsavel_id
        form = CobrancaForm(
            request.POST, instance=cobranca, usuario=request.user, organization=org
        )
        if form.is_valid():
            cobranca = form.save(commit=False)
            preserve_organization(cobranca, original_org_id)
            cobranca.save()
            atualizar_cobranca(
                cobranca,
                autor=request.user,
                vencimento_anterior=vencimento_anterior,
                responsavel_anterior_id=responsavel_anterior_id,
            )
            messages.add_message(
                request, constants.SUCCESS, "Cobrança atualizada com sucesso."
            )
            return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)
    else:
        form = CobrancaForm(
            instance=cobranca,
            usuario=request.user,
            organization=cobranca.organization,
        )

    return render(
        request,
        "financeiro/cobranca_form.html",
        {"form": form, "titulo": "Editar cobrança", "cobranca": cobranca},
    )


@perm_cancelar_cobrancas
@login_required
def cobranca_cancelar(request, pk):
    cobranca = _get_cobranca(request, pk)
    if cobranca.status == StatusCobranca.CANCELED:
        return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)

    if request.method == "POST":
        motivo = (request.POST.get("motivo") or "").strip()
        cancelar_cobranca(cobranca, autor=request.user, motivo=motivo)
        messages.add_message(request, constants.SUCCESS, "Cobrança cancelada.")
        return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)

    return render(
        request,
        "financeiro/cobranca_cancelar.html",
        {"cobranca": cobranca},
    )


@perm_registrar_recebimentos
@login_required
def cobranca_registrar_recebimento(request, pk):
    cobranca = _get_cobranca(request, pk)
    if cobranca.status in (StatusCobranca.CANCELED, StatusCobranca.DRAFT):
        messages.add_message(
            request,
            constants.ERROR,
            "Esta cobrança não aceita recebimentos.",
        )
        return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)
    if cobranca.saldo <= 0:
        messages.add_message(request, constants.INFO, "Esta cobrança já está quitada.")
        return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)

    if request.method == "POST":
        org, denied = reject_or_organization(
            request, "financeiro_cobranca_detalhe", pk=cobranca.pk
        )
        if denied:
            return denied
        if not parent_in_organization(cobranca, org):
            messages.add_message(request, constants.ERROR, MSG_PARENT_TENANT)
            return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)
        form = RecebimentoForm(request.POST, cobranca=cobranca)
        if form.is_valid():
            registrar_recebimento(
                cobranca,
                valor=form.cleaned_data["valor"],
                data_recebimento=form.cleaned_data["data_recebimento"],
                forma_pagamento=form.cleaned_data["forma_pagamento"],
                referencia=form.cleaned_data.get("referencia", ""),
                observacao=form.cleaned_data.get("observacao", ""),
                autor=request.user,
                organization=org,
            )
            messages.add_message(
                request, constants.SUCCESS, "Recebimento registrado com sucesso."
            )
            return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)
    else:
        form = RecebimentoForm(cobranca=cobranca)

    return render(
        request,
        "financeiro/cobranca_recebimento_form.html",
        {"form": form, "cobranca": cobranca},
    )


@perm_registrar_recebimentos
@login_required
def cobranca_estornar_recebimento(request, pk, recebimento_id):
    cobranca = _get_cobranca(request, pk)
    recebimento = get_object_or_404(
        CobrancaRecebimento,
        pk=recebimento_id,
        cobranca=cobranca,
        organization=cobranca.organization,
    )

    if request.method == "POST":
        motivo = (request.POST.get("motivo") or "").strip()
        try:
            estornar_recebimento(recebimento, autor=request.user, motivo=motivo)
        except ValidationError as exc:
            msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            messages.add_message(request, constants.ERROR, msg)
            return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)
        messages.add_message(request, constants.SUCCESS, "Recebimento estornado.")
        return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)

    return render(
        request,
        "financeiro/cobranca_estornar_recebimento.html",
        {"cobranca": cobranca, "recebimento": recebimento},
    )


@perm_editar_cobrancas
@login_required
def cobranca_agendar_agenda(request, pk):
    cobranca = _get_cobranca(request, pk)
    if cobranca.status in (StatusCobranca.CANCELED, StatusCobranca.DRAFT):
        messages.add_message(
            request, constants.ERROR, "Esta cobrança não pode ser agendada."
        )
        return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)
    if cobranca.saldo <= 0:
        messages.add_message(request, constants.INFO, "Cobrança já quitada.")
        return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)

    if request.method == "POST":
        tipo = (request.POST.get("tipo") or "tarefa").strip()
        try:
            if tipo == "compromisso":
                criar_compromisso_cobranca(cobranca, autor=request.user)
                msg = "Compromisso criado na agenda."
            else:
                criar_tarefa_cobranca(cobranca, autor=request.user)
                msg = "Tarefa de follow-up criada na agenda."
        except ValidationError as exc:
            msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            messages.add_message(request, constants.ERROR, msg)
            return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)
        messages.add_message(request, constants.SUCCESS, msg)
        return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)

    return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)


@perm_editar_cobrancas
@login_required
def cobranca_cobrar(request, pk):
    cobranca = _get_cobranca(request, pk)
    if cobranca.status in (StatusCobranca.CANCELED, StatusCobranca.DRAFT):
        messages.add_message(
            request,
            constants.ERROR,
            "Esta cobrança não permite envio de mensagem de cobrança.",
        )
        return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)
    if cobranca.saldo <= 0:
        messages.add_message(request, constants.INFO, "Esta cobrança já está quitada.")
        return redirect("financeiro_cobranca_detalhe", pk=cobranca.pk)

    contexto = montar_contexto_mensagem(cobranca)
    tom = (request.POST.get("tom") or request.GET.get("tom") or TomMensagemCobranca.CORDIAL).strip()
    if tom not in TomMensagemCobranca.values:
        tom = TomMensagemCobranca.CORDIAL

    mensagem = gerar_mensagem_padrao(contexto, tom=tom)

    if request.method == "POST":
        acao = (request.POST.get("acao") or "").strip()
        if acao == "gerar_ia":
            try:
                mensagem = gerar_mensagem_ia(cobranca, tom=tom, autor=request.user)
                messages.add_message(
                    request,
                    constants.SUCCESS,
                    "Mensagem gerada com IA. Revise antes de enviar ao cliente.",
                )
            except CobrancaMensagemError as exc:
                messages.add_message(request, constants.ERROR, str(exc))
        elif acao == "atualizar":
            mensagem = gerar_mensagem_padrao(contexto, tom=tom)
        else:
            mensagem = (request.POST.get("mensagem") or mensagem).strip()

    return render(
        request,
        "financeiro/cobranca_cobrar.html",
        {
            "cobranca": cobranca,
            "contexto": contexto,
            "mensagem": mensagem,
            "tom": tom,
            "tom_choices": TomMensagemCobranca.choices,
            "ia_disponivel": ia_disponivel(),
        },
    )


@perm_ver_cobrancas
@login_required
def cobranca_auditoria(request):
    org = _billing_organization(request)
    eventos = historico_auditoria_organization(org)
    resumo = resumo_auditoria_organization(org)
    return render(
        request,
        "financeiro/cobrancas_auditoria.html",
        {"eventos": eventos, "resumo_auditoria": resumo},
    )
