from datetime import datetime, time, timedelta
import json

from urllib.parse import urlencode

from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.contrib.auth import authenticate, login
from django.contrib import auth
from django.contrib.messages import constants
from django.contrib import messages
from django.urls import reverse
from .forms import CompromissoForm, TarefaForm
from .models import AgendaLembrete, Cliente, Compromisso, Documentos, Tarefa
from .choices import EscopoAgenda, OrigemLead, Prioridade, Recorrencia, StatusCompromisso, StatusTarefa, TipoCompromisso
from .services.agenda import (
    AgendaFiltros,
    DIAS_PRAZOS_PROXIMOS,
    aplicar_status_compromisso,
    aplicar_status_tarefa,
    calcular_kpis,
    clientes_para_filtro,
    compromissos_para_agenda,
    confirmar_compromisso,
    contexto_visualizacao,
    itens_atencao,
    nao_compareceu_compromisso,
    registrar_audit,
    registrar_edicao_compromisso,
    tarefas_para_agenda,
)
from .services.agenda_equipe import membros_agenda, processos_distintos, processos_por_cliente
from .services.agenda_auditoria import (
    detalhe_alteracao,
    historico_auditoria_usuario,
    resumo_auditoria_usuario,
    rotulo_item_auditoria,
)
from .permissions import (
    permissoes_agenda,
    pode_cancelar_agenda,
    pode_criar_agenda,
    pode_editar_agenda,
    pode_ver_agenda,
    pode_ver_auditoria_agenda,
)
from .decorators import login_e_perm_agenda
from .services.agenda_ia import (
    AgendaIaError,
    gerar_resumo_ia,
    limpar_resumo_ia_sessao,
    montar_contexto_resumo_dia,
    obter_resumo_dia,
    salvar_resumo_ia_sessao,
)
from .services.agenda_cliente import (
    compromissos_cliente,
    hook_financeiro_cliente,
    resumo_agenda_cliente,
    sugestoes_crm_cliente,
    tarefas_cliente,
    url_agenda_cliente,
)
from .services.compromisso_lembrete import (
    cancelar_lembrete_compromisso,
    garantir_cron_series_recorrentes,
    sincronizar_lembrete_compromisso,
    sincronizar_lembretes_serie,
)
from .services.compromisso_recorrencia import gerar_ocorrencias_serie
from django.contrib.auth.decorators import login_required
from marketing.services.atribuicao import (
    aplicar_atribuicao_em_cliente,
    capturar_atribuicao_sessao,
    mesclar_atribuicao_sessao,
    resolver_atribuicao,
)
from .document_text import extract_document_text

def cadastro(request):
    if request.method == "GET":
        return render(request, "cadastro.html")

    if request.method == "POST":
        username = request.POST.get("username")
        email = request.POST.get("email") or ""
        password = request.POST.get("password") or request.POST.get("senha")
        confirm_password = request.POST.get("confirm_password") or request.POST.get(
            "confirmar_senha"
        )

        if not username:
            messages.add_message(request, constants.ERROR, "Informe um username")
            return redirect("cadastro")

        if not password or not confirm_password:
            messages.add_message(request, constants.ERROR, "Informe e confirme a senha")
            return redirect("cadastro")

        if password != confirm_password:
            messages.add_message(request, constants.ERROR, "As senhas não conferem")
            return redirect("cadastro")

        if len(password) < 6:
            messages.add_message(request, constants.ERROR, "A senha deve ter pelo menos 6 caracteres")
            return redirect("cadastro")

        if User.objects.filter(username=username).exists():
            messages.add_message(request, constants.ERROR, "Usuário já existe")
            return redirect("cadastro")

        User.objects.create_user(
            username=username,
            email=email,
            password=password,
        )
        messages.add_message(request, constants.SUCCESS, "Cadastro realizado com sucesso")
        return redirect("login")


def login_view(request):
    if request.method == "GET":
        return render(request, "login.html")

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password") or request.POST.get("senha")

        if not username or not password:
            messages.add_message(request, constants.ERROR, "Informe username e senha")
            return redirect("login")

        user = authenticate(request, username=username, password=password)
        if user is None:
            messages.add_message(request, constants.ERROR, "Credenciais inválidas")
            return redirect("login")

        login(request, user)
        messages.add_message(request, constants.SUCCESS, "Login realizado com sucesso")
        return redirect("clientes")


@login_required
def logout_view(request):
    if request.method == "POST":
        auth.logout(request)
        messages.add_message(request, constants.SUCCESS, "Logout realizado com sucesso")
    return redirect("login")


@login_required
def clientes(request):
    capturar_atribuicao_sessao(request)
    if request.method == 'GET':
        base = Cliente.objects.filter(user=request.user)
        status_param = (request.GET.get("status") or "").strip()
        q = (request.GET.get("q") or "").strip()

        clientes = base
        if status_param == "ativo":
            clientes = clientes.filter(status="ativo")
        elif status_param == "inativo":
            clientes = clientes.filter(status="inativo")
        elif status_param == "em_prospeccao":
            clientes = clientes.filter(status="em_prospeccao")

        if q:
            clientes = clientes.filter(Q(nome__icontains=q) | Q(email__icontains=q))

        mkt_param = (request.GET.get("mkt") or "").strip()
        periodo_mkt = None
        mkt_filter_label = ""
        if mkt_param:
            from marketing.services.resultados_operacional import (
                aplicar_filtro_mkt_clientes,
                filtro_mkt_valido,
                periodo_from_request_get,
                rotulo_filtro_mkt,
            )

            if filtro_mkt_valido(mkt_param):
                periodo_mkt = periodo_from_request_get(request.GET)
                clientes = aplicar_filtro_mkt_clientes(
                    request.user, clientes, mkt_param, periodo_mkt
                )
                mkt_filter_label = rotulo_filtro_mkt(mkt_param, periodo_mkt)

        atrib_sessao = mesclar_atribuicao_sessao(request, request.GET)

        return render(
            request,
            "clientes.html",
            {
                "clientes": clientes,
                "total": base.count(),
                "prospects": base.filter(status="em_prospeccao").count(),
                "ativos": base.filter(status="ativo").count(),
                "inativos": base.filter(status="inativo").count(),
                "status_filter": status_param,
                "q": q,
                "mkt_filter": mkt_param,
                "mkt_filter_label": mkt_filter_label,
                "atrib_sessao": atrib_sessao,
                "origem_choices": [
                    (v, lbl) for v, lbl in OrigemLead.choices if v
                ],
            },
        )
    elif request.method == 'POST':
        nome = request.POST.get('nome')
        email = request.POST.get('email')
        telefone = (request.POST.get("telefone") or "").strip()
        endereco = (request.POST.get("endereco") or "").strip()
        tipo = request.POST.get('tipo')
        status = (request.POST.get("status") or "em_prospeccao").strip()
        fase_funil = (request.POST.get("fase_funil") or "primeiro_contato").strip()
        data_relatorio_prospeccao = parse_date((request.POST.get("data_relatorio_prospeccao") or "").strip())
        relatorio_prospeccao = (request.POST.get("relatorio_prospeccao") or "").strip()
        origem_manual = (request.POST.get("origem") or "").strip()
        if status not in {"em_prospeccao", "ativo", "inativo"}:
            status = "em_prospeccao"
        fases_validas = {"primeiro_contato", "proposta_enviada", "aguardando_decisao", "novo_contato"}
        if fase_funil not in fases_validas:
            fase_funil = "primeiro_contato"
        if status != "em_prospeccao":
            fase_funil = "primeiro_contato"
            data_relatorio_prospeccao = None
            relatorio_prospeccao = ""

        params_atrib = mesclar_atribuicao_sessao(request, request.POST)
        atrib = resolver_atribuicao(params_atrib, origem_manual=origem_manual)

        cliente_novo = Cliente(
            nome=nome,
            email=email,
            telefone=telefone,
            endereco=endereco,
            tipo=tipo,
            status=status,
            fase_funil=fase_funil,
            data_relatorio_prospeccao=data_relatorio_prospeccao,
            relatorio_prospeccao=relatorio_prospeccao,
            user=request.user,
        )
        aplicar_atribuicao_em_cliente(cliente_novo, atrib)
        cliente_novo.save()

        messages.add_message(request, constants.SUCCESS, 'Cliente cadastrado com sucesso!')
        return redirect('clientes')


@login_required
def cliente_home(request):
    """
    Evita 404 em /usuarios/cliente/ (rota real exige id: /usuarios/cliente/<id>).
    """
    return redirect("clientes")


def _contexto_financeiro_cliente(user, cliente_obj):
    from financeiro.permissions import pode_ver_cobrancas
    from financeiro.services.cliente_financeiro import (
        cobrancas_cliente,
        contratos_cliente,
        resumo_financeiro_cliente,
        url_cobrancas_cliente,
        url_nova_cobranca_cliente,
        url_novo_contrato_cliente,
    )

    if not pode_ver_cobrancas(user):
        return {"financeiro_oculto": True}

    return {
        "financeiro_oculto": False,
        "financeiro_resumo": resumo_financeiro_cliente(user, cliente_obj),
        "financeiro_contratos": contratos_cliente(user, cliente_obj),
        "financeiro_cobrancas": cobrancas_cliente(user, cliente_obj),
        "url_cobrancas_cliente": url_cobrancas_cliente(cliente_obj.id),
        "url_nova_cobranca": url_nova_cobranca_cliente(cliente_obj.id),
        "url_novo_contrato": url_novo_contrato_cliente(cliente_obj.id),
    }


def _contexto_agenda_cliente(user, cliente_obj):
    return {
        "agenda_resumo": resumo_agenda_cliente(user, cliente_obj),
        "agenda_compromissos": compromissos_cliente(user, cliente_obj),
        "agenda_tarefas": tarefas_cliente(user, cliente_obj),
        "sugestoes_crm": sugestoes_crm_cliente(user, cliente_obj),
        "hook_financeiro": hook_financeiro_cliente(user, cliente_obj),
        "url_agenda_compromisso": url_agenda_cliente(cliente_obj.id, modal="compromisso"),
        "url_agenda_tarefa": url_agenda_cliente(cliente_obj.id, modal="tarefa"),
        "url_agenda_completa": url_agenda_cliente(cliente_obj.id),
    }


def _aplicar_cliente_inicial_agenda(user, filtros, compromisso_form, tarefa_form):
    if filtros.cliente_id:
        if Cliente.objects.filter(pk=filtros.cliente_id, user=user).exists():
            compromisso_form.fields["cliente"].initial = filtros.cliente_id
            tarefa_form.fields["cliente"].initial = filtros.cliente_id
    if filtros.tipo:
        tipos_validos = {choice.value for choice in TipoCompromisso}
        if filtros.tipo in tipos_validos:
            compromisso_form.fields["tipo"].initial = filtros.tipo


def _carregar_form_compromisso(request, *, instance=None):
    if request.method == "POST":
        data = request.POST
    else:
        data = None
    return CompromissoForm(data, instance=instance, user=request.user)


def _carregar_form_tarefa(request, *, instance=None):
    if request.method == "POST":
        data = request.POST
    else:
        data = None
    return TarefaForm(data, instance=instance, user=request.user)


def _instancia_edicao_agenda(request, abrir_modal):
    compromisso = None
    tarefa = None
    if request.method != "GET":
        return compromisso, tarefa
    if abrir_modal == "compromisso":
        raw_id = (request.GET.get("compromisso_id") or "").strip()
        if raw_id.isdigit():
            compromisso = Compromisso.objects.filter(
                pk=int(raw_id), user=request.user
            ).first()
    elif abrir_modal == "tarefa":
        raw_id = (request.GET.get("tarefa_id") or "").strip()
        if raw_id.isdigit():
            tarefa = Tarefa.objects.filter(pk=int(raw_id), user=request.user).first()
    return compromisso, tarefa


@login_required
def cliente(request, id):
    cliente_obj = get_object_or_404(Cliente, id=id, user=request.user)
    if request.method == 'GET':
        documentos = Documentos.objects.filter(cliente=cliente_obj)
        return render(
            request,
            'cliente.html',
            {
                'cliente': cliente_obj,
                'documentos': documentos,
                **_contexto_agenda_cliente(request.user, cliente_obj),
                **_contexto_financeiro_cliente(request.user, cliente_obj),
            },
        )
    elif request.method == 'POST':
        action = (request.POST.get("action") or "").strip()

        if action == "atualizar_prospeccao":
            if cliente_obj.status != "em_prospeccao":
                messages.add_message(
                    request,
                    constants.ERROR,
                    "O cliente precisa estar em prospecção para atualizar o funil.",
                )
                return redirect(reverse("cliente", kwargs={"id": cliente_obj.id}))

            fase_funil = (request.POST.get("fase_funil") or "primeiro_contato").strip()
            fases_validas = {"primeiro_contato", "proposta_enviada", "aguardando_decisao", "novo_contato"}
            if fase_funil not in fases_validas:
                fase_funil = "primeiro_contato"

            cliente_obj.fase_funil = fase_funil
            cliente_obj.data_relatorio_prospeccao = parse_date(
                (request.POST.get("data_relatorio_prospeccao") or "").strip()
            )
            cliente_obj.relatorio_prospeccao = (request.POST.get("relatorio_prospeccao") or "").strip()
            cliente_obj.save(
                update_fields=[
                    "fase_funil",
                    "data_relatorio_prospeccao",
                    "relatorio_prospeccao",
                ]
            )
            messages.add_message(request, constants.SUCCESS, "Funil e relatório de prospecção atualizados.")
            return redirect(reverse("cliente", kwargs={"id": cliente_obj.id}))

        tipo = request.POST.get('tipo')
        documento = request.FILES.get('documento')
        data_str = request.POST.get('data')

        if not documento:
            messages.add_message(request, constants.ERROR, "Selecione um arquivo para enviar.")
            return redirect(reverse("cliente", kwargs={"id": cliente_obj.id}))

        data_upload = timezone.now()
        if data_str:
            d = parse_date(data_str)
            if d:
                naive = datetime.combine(d, time.min)
                data_upload = (
                    timezone.make_aware(naive)
                    if timezone.is_naive(naive)
                    else naive
                )

        Documentos.objects.create(
            cliente=cliente_obj,
            tipo=tipo or "O",
            arquivo=documento,
            data_upload=data_upload,
            content=extract_document_text(documento, documento.name),
        )

        messages.add_message(request, constants.SUCCESS, "Documento enviado com sucesso.")
        return redirect(reverse('cliente', kwargs={'id': cliente_obj.id}))


def _redirect_agenda(filtros: AgendaFiltros, modal: str = ""):
    params = filtros.to_query_dict()
    if modal:
        params["modal"] = modal
    url = reverse("agenda")
    qs = urlencode(params)
    if qs:
        url = f"{url}?{qs}"
    return redirect(url)


def _negar_agenda(request, filtros: AgendaFiltros, mensagem: str):
    messages.add_message(request, constants.ERROR, mensagem)
    return _redirect_agenda(filtros)


def _verificar_perm_post_agenda(request, filtros: AgendaFiltros, action: str):
    if not pode_ver_agenda(request.user):
        return _negar_agenda(request, filtros, "Sem permissão para acessar a agenda.")
    criar = {"criar_compromisso", "criar_tarefa"}
    editar = {
        "editar_compromisso",
        "editar_tarefa",
        "toggle_tarefa",
        "iniciar_tarefa",
        "concluir_tarefa",
        "confirmar_compromisso",
        "nao_compareceu_compromisso",
        "realizar_compromisso",
    }
    cancelar = {"excluir_tarefa", "excluir_compromisso"}
    if action in criar and not pode_criar_agenda(request.user):
        return _negar_agenda(
            request, filtros, "Sem permissão para criar compromissos ou tarefas."
        )
    if action in editar and not pode_editar_agenda(request.user):
        return _negar_agenda(request, filtros, "Sem permissão para editar a agenda.")
    if action in cancelar and not pode_cancelar_agenda(request.user):
        return _negar_agenda(
            request, filtros, "Sem permissão para cancelar compromissos ou tarefas."
        )
    return None


def _pos_salvar_compromisso(compromisso: Compromisso, *, criado: bool = False) -> None:
    if criado:
        ocorrencias = gerar_ocorrencias_serie(compromisso)
        sincronizar_lembrete_compromisso(compromisso)
        if ocorrencias:
            sincronizar_lembretes_serie(ocorrencias)
        if compromisso.recorrencia != Recorrencia.NAO_REPETIR:
            garantir_cron_series_recorrentes()
        return
    sincronizar_lembrete_compromisso(compromisso)


@login_required
def agenda(request):
    if not pode_ver_agenda(request.user):
        messages.add_message(
            request,
            constants.ERROR,
            "Sem permissão para visualizar a agenda.",
        )
        return redirect("clientes")

    filtros = AgendaFiltros.from_request(request)
    agenda_query = filtros.query_string()
    filtros_base = filtros.to_query_dict()
    filtros_base.pop("view", None)
    filtros_query_base = urlencode(filtros_base)

    abrir_modal = (request.GET.get("modal") or "").strip()
    compromisso_editar = None
    tarefa_editar = None
    compromisso_form = None
    tarefa_form = None

    if request.method == "POST":
        action = request.POST.get("action")
        filtros = AgendaFiltros.from_request(request)
        negado = _verificar_perm_post_agenda(request, filtros, action)
        if negado:
            return negado

        if action == "criar_compromisso":
            compromisso_form = _carregar_form_compromisso(request)
            if compromisso_form.is_valid():
                compromisso = compromisso_form.save()
                _pos_salvar_compromisso(compromisso, criado=True)
                registrar_audit(
                    usuario=request.user,
                    item_tipo="compromisso",
                    item_id=compromisso.pk,
                    acao="criado",
                )
                messages.add_message(
                    request, constants.SUCCESS, "Compromisso criado com sucesso."
                )
                return _redirect_agenda(filtros)
            abrir_modal = "compromisso"
            agenda_query = filtros.query_string()
            err = next(iter(compromisso_form.non_field_errors()), "")
            if not err and compromisso_form.errors:
                first_field = next(iter(compromisso_form.errors))
                err = compromisso_form.errors[first_field][0]
            messages.add_message(
                request,
                constants.ERROR,
                err or "Corrija os erros do formulário de compromisso.",
            )

        elif action == "editar_compromisso":
            compromisso_id = request.POST.get("compromisso_id")
            compromisso = get_object_or_404(
                Compromisso, id=compromisso_id, user=request.user
            )
            prazo_oficial_antes = compromisso.prazo_oficial
            prazo_interno_antes = compromisso.prazo_interno
            responsavel_antes = compromisso.responsavel_id
            compromisso_form = _carregar_form_compromisso(
                request, instance=compromisso
            )
            if compromisso_form.is_valid():
                compromisso = compromisso_form.save()
                _pos_salvar_compromisso(compromisso)
                registrar_edicao_compromisso(
                    request.user,
                    compromisso,
                    prazo_oficial_antes=prazo_oficial_antes,
                    prazo_interno_antes=prazo_interno_antes,
                    responsavel_antes_id=responsavel_antes,
                )
                messages.add_message(
                    request, constants.SUCCESS, "Compromisso atualizado com sucesso."
                )
                return _redirect_agenda(filtros)
            abrir_modal = "compromisso"
            compromisso_editar = compromisso
            agenda_query = filtros.query_string()
            err = next(iter(compromisso_form.non_field_errors()), "")
            if not err and compromisso_form.errors:
                first_field = next(iter(compromisso_form.errors))
                err = compromisso_form.errors[first_field][0]
            messages.add_message(
                request,
                constants.ERROR,
                err or "Corrija os erros do formulário de compromisso.",
            )

        elif action == "criar_tarefa":
            tarefa_form = _carregar_form_tarefa(request)
            if tarefa_form.is_valid():
                tarefa = tarefa_form.save()
                registrar_audit(
                    usuario=request.user,
                    item_tipo="tarefa",
                    item_id=tarefa.pk,
                    acao="criado",
                )
                messages.add_message(
                    request, constants.SUCCESS, "Tarefa criada com sucesso."
                )
                return _redirect_agenda(filtros)
            abrir_modal = "tarefa"
            agenda_query = filtros.query_string()
            err = next(iter(tarefa_form.non_field_errors()), "")
            if not err and tarefa_form.errors:
                first_field = next(iter(tarefa_form.errors))
                err = tarefa_form.errors[first_field][0]
            messages.add_message(
                request,
                constants.ERROR,
                err or "Corrija os erros do formulário de tarefa.",
            )

        elif action == "editar_tarefa":
            tarefa_id = request.POST.get("tarefa_id")
            tarefa = get_object_or_404(Tarefa, id=tarefa_id, user=request.user)
            tarefa_form = _carregar_form_tarefa(request, instance=tarefa)
            if tarefa_form.is_valid():
                tarefa = tarefa_form.save()
                registrar_audit(
                    usuario=request.user,
                    item_tipo="tarefa",
                    item_id=tarefa.pk,
                    acao="alterado",
                )
                messages.add_message(
                    request, constants.SUCCESS, "Tarefa atualizada com sucesso."
                )
                return _redirect_agenda(filtros)
            abrir_modal = "tarefa"
            tarefa_editar = tarefa
            agenda_query = filtros.query_string()
            err = next(iter(tarefa_form.non_field_errors()), "")
            if not err and tarefa_form.errors:
                first_field = next(iter(tarefa_form.errors))
                err = tarefa_form.errors[first_field][0]
            messages.add_message(
                request,
                constants.ERROR,
                err or "Corrija os erros do formulário de tarefa.",
            )

        elif action == "toggle_tarefa":
            tarefa_id = request.POST.get("tarefa_id")
            tarefa = get_object_or_404(Tarefa, id=tarefa_id, user=request.user)
            if tarefa.status == StatusTarefa.CONCLUIDA:
                aplicar_status_tarefa(
                    tarefa, StatusTarefa.PENDENTE, usuario=request.user
                )
            else:
                aplicar_status_tarefa(
                    tarefa, StatusTarefa.CONCLUIDA, usuario=request.user
                )
            return _redirect_agenda(filtros)

        elif action == "iniciar_tarefa":
            tarefa_id = request.POST.get("tarefa_id")
            tarefa = get_object_or_404(Tarefa, id=tarefa_id, user=request.user)
            if aplicar_status_tarefa(
                tarefa, StatusTarefa.EM_ANDAMENTO, usuario=request.user
            ):
                messages.add_message(
                    request, constants.SUCCESS, "Tarefa iniciada."
                )
            return _redirect_agenda(filtros)

        elif action == "concluir_tarefa":
            tarefa_id = request.POST.get("tarefa_id")
            tarefa = get_object_or_404(Tarefa, id=tarefa_id, user=request.user)
            if aplicar_status_tarefa(
                tarefa, StatusTarefa.CONCLUIDA, usuario=request.user
            ):
                messages.add_message(
                    request, constants.SUCCESS, "Tarefa concluída."
                )
            return _redirect_agenda(filtros)

        elif action == "confirmar_compromisso":
            compromisso_id = request.POST.get("compromisso_id")
            compromisso = get_object_or_404(
                Compromisso, id=compromisso_id, user=request.user
            )
            if confirmar_compromisso(compromisso, usuario=request.user):
                messages.add_message(
                    request, constants.SUCCESS, "Compromisso confirmado."
                )
            return _redirect_agenda(filtros)

        elif action == "nao_compareceu_compromisso":
            compromisso_id = request.POST.get("compromisso_id")
            compromisso = get_object_or_404(
                Compromisso, id=compromisso_id, user=request.user
            )
            if nao_compareceu_compromisso(compromisso, usuario=request.user):
                messages.add_message(
                    request,
                    constants.SUCCESS,
                    "Compromisso registrado como não compareceu.",
                )
            return _redirect_agenda(filtros)

        elif action == "realizar_compromisso":
            compromisso_id = request.POST.get("compromisso_id")
            compromisso = get_object_or_404(
                Compromisso, id=compromisso_id, user=request.user
            )
            if aplicar_status_compromisso(
                compromisso, StatusCompromisso.REALIZADO, usuario=request.user
            ):
                messages.add_message(
                    request, constants.SUCCESS, "Compromisso marcado como realizado."
                )
            return _redirect_agenda(filtros)

        elif action == "excluir_tarefa":
            tarefa_id = request.POST.get("tarefa_id")
            tarefa = get_object_or_404(Tarefa, id=tarefa_id, user=request.user)
            aplicar_status_tarefa(
                tarefa, StatusTarefa.CANCELADA, usuario=request.user
            )
            messages.add_message(request, constants.SUCCESS, "Tarefa cancelada.")
            return _redirect_agenda(filtros)

        elif action == "excluir_compromisso":
            compromisso_id = request.POST.get("compromisso_id")
            compromisso = get_object_or_404(
                Compromisso, id=compromisso_id, user=request.user
            )
            cancelar_lembrete_compromisso(compromisso)
            compromisso.cancelar()
            registrar_audit(
                usuario=request.user,
                item_tipo="compromisso",
                item_id=compromisso.pk,
                acao="cancelado",
            )
            messages.add_message(request, constants.SUCCESS, "Compromisso cancelado.")
            return _redirect_agenda(filtros)

        elif action == "marcar_lembrete_lido":
            lembrete_id = request.POST.get("lembrete_id")
            lembrete = get_object_or_404(
                AgendaLembrete, id=lembrete_id, user=request.user
            )
            lembrete.lido = True
            lembrete.save(update_fields=["lido"])
            return _redirect_agenda(filtros)

        elif action == "marcar_lembretes_lidos":
            AgendaLembrete.objects.filter(user=request.user, lido=False).update(
                lido=True
            )
            return _redirect_agenda(filtros)

        elif action == "gerar_resumo_dia_ia":
            ctx = montar_contexto_resumo_dia(
                request.user, filtros.data_referencia, filtros=filtros
            )
            try:
                resumo = gerar_resumo_ia(ctx)
                salvar_resumo_ia_sessao(
                    request,
                    request.user.pk,
                    filtros.data_referencia,
                    resumo,
                )
                messages.add_message(
                    request,
                    constants.SUCCESS,
                    "Resumo do dia gerado com IA.",
                )
            except AgendaIaError as exc:
                messages.add_message(request, constants.ERROR, str(exc))
            return _redirect_agenda(filtros)

        elif action == "limpar_resumo_dia_ia":
            limpar_resumo_ia_sessao(
                request, request.user.pk, filtros.data_referencia
            )
            messages.add_message(
                request,
                constants.SUCCESS,
                "Voltando ao resumo padrão.",
            )
            return _redirect_agenda(filtros)

    if compromisso_form is None:
        if compromisso_editar is None:
            compromisso_editar, _ = _instancia_edicao_agenda(request, abrir_modal)
        compromisso_form = _carregar_form_compromisso(
            request, instance=compromisso_editar
        )
    if tarefa_form is None:
        if tarefa_editar is None:
            _, tarefa_editar = _instancia_edicao_agenda(request, abrir_modal)
        tarefa_form = _carregar_form_tarefa(request, instance=tarefa_editar)

    _aplicar_cliente_inicial_agenda(
        request.user, filtros, compromisso_form, tarefa_form
    )

    compromissos = compromissos_para_agenda(request.user, filtros)
    tarefas = tarefas_para_agenda(request.user, filtros)
    kpis = calcular_kpis(request.user, filtros.data_referencia)

    context = {
        "compromissos": compromissos,
        "tarefas": tarefas,
        "kpis": kpis,
        "kpis_dias_proximos": DIAS_PRAZOS_PROXIMOS,
        "itens_atencao": itens_atencao(request.user, filtros.data_referencia),
        "view_ativa": filtros.view,
        "data_referencia": filtros.data_referencia,
        "filtro_data": filtros.data.isoformat() if filtros.data else "",
        "filtro_cliente": str(filtros.cliente_id) if filtros.cliente_id else "",
        "filtro_tipo": filtros.tipo,
        "filtro_responsavel": str(filtros.responsavel_id) if filtros.responsavel_id else "",
        "filtro_status_compromisso": filtros.status_compromisso,
        "status_tarefa": filtros.status_tarefa,
        "filtro_prioridade": filtros.prioridade,
        "filtro_escopo": filtros.escopo,
        "filtro_processo": filtros.processo,
        "escopo_choices": EscopoAgenda.choices,
        "processos_filtro": processos_distintos(request.user),
        "processos_por_cliente_json": json.dumps(processos_por_cliente(request.user)),
        "filtros_query_base": filtros_query_base,
        "agenda_query": agenda_query,
        "clientes_filtro": clientes_para_filtro(request.user),
        "responsaveis_filtro": membros_agenda(request.user),
        "tipo_choices": TipoCompromisso.choices,
        "status_compromisso_choices": StatusCompromisso.choices,
        "prioridade_choices": Prioridade.choices,
        "abrir_modal": abrir_modal,
        "compromisso_editar_id": (
            compromisso_editar.pk
            if compromisso_editar
            else (compromisso_form.instance.pk if compromisso_form.instance.pk else None)
        ),
        "tarefa_editar_id": (
            tarefa_editar.pk
            if tarefa_editar
            else (tarefa_form.instance.pk if tarefa_form.instance.pk else None)
        ),
        "compromisso_form": compromisso_form,
        "tarefa_form": tarefa_form,
        "permissoes_agenda": permissoes_agenda(request.user),
        "lembretes_pendentes": AgendaLembrete.objects.filter(
            user=request.user, lido=False
        ).select_related("compromisso")[:12],
        "resumo_dia": obter_resumo_dia(
            request.user,
            filtros.data_referencia,
            request=request,
            filtros=filtros,
        ),
        **contexto_visualizacao(filtros, compromissos, tarefas),
    }
    return render(request, "agenda.html", context)


@login_e_perm_agenda(
    "usuarios.view_audit_agenda",
    mensagem="Sem permissão para visualizar a auditoria da agenda.",
)
def agenda_auditoria(request):
    eventos = historico_auditoria_usuario(request.user, limit=150)
    eventos_ctx = [
        {
            "log": log,
            "item_titulo": rotulo_item_auditoria(log),
            "detalhe": detalhe_alteracao(log),
        }
        for log in eventos
    ]
    return render(
        request,
        "agenda_auditoria.html",
        {
            "resumo_auditoria": resumo_auditoria_usuario(request.user),
            "eventos": eventos_ctx,
            "permissoes_agenda": permissoes_agenda(request.user),
            "view_ativa": "",
            "filtros_query_base": "",
        },
    )

