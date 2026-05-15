from datetime import datetime, time, timedelta

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
from .models import Cliente, Compromisso, Documentos, Tarefa
from django.contrib.auth.decorators import login_required
from .document_text import extract_document_text


# Create your views here.

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
        if status not in {"em_prospeccao", "ativo", "inativo"}:
            status = "em_prospeccao"
        fases_validas = {"primeiro_contato", "proposta_enviada", "aguardando_decisao", "novo_contato"}
        if fase_funil not in fases_validas:
            fase_funil = "primeiro_contato"
        if status != "em_prospeccao":
            fase_funil = "primeiro_contato"
            data_relatorio_prospeccao = None
            relatorio_prospeccao = ""

        Cliente.objects.create(
            nome=nome,
            email=email,
            telefone=telefone,
            endereco=endereco,
            tipo=tipo,
            status=status,
            fase_funil=fase_funil,
            data_relatorio_prospeccao=data_relatorio_prospeccao,
            relatorio_prospeccao=relatorio_prospeccao,
            user=request.user
        )

        messages.add_message(request, constants.SUCCESS, 'Cliente cadastrado com sucesso!')
        return redirect('clientes')


@login_required
def cliente_home(request):
    """
    Evita 404 em /usuarios/cliente/ (rota real exige id: /usuarios/cliente/<id>).
    """
    return redirect("clientes")


def cliente(request, id):
    cliente = get_object_or_404(Cliente, id=id)
    if request.method == 'GET':
        documentos = Documentos.objects.filter(cliente=cliente)
        return render(request, 'cliente.html', {'cliente': cliente, 'documentos': documentos})
    elif request.method == 'POST':
        action = (request.POST.get("action") or "").strip()

        if action == "atualizar_prospeccao":
            if cliente.status != "em_prospeccao":
                messages.add_message(
                    request,
                    constants.ERROR,
                    "O cliente precisa estar em prospecção para atualizar o funil.",
                )
                return redirect(reverse("cliente", kwargs={"id": cliente.id}))

            fase_funil = (request.POST.get("fase_funil") or "primeiro_contato").strip()
            fases_validas = {"primeiro_contato", "proposta_enviada", "aguardando_decisao", "novo_contato"}
            if fase_funil not in fases_validas:
                fase_funil = "primeiro_contato"

            cliente.fase_funil = fase_funil
            cliente.data_relatorio_prospeccao = parse_date(
                (request.POST.get("data_relatorio_prospeccao") or "").strip()
            )
            cliente.relatorio_prospeccao = (request.POST.get("relatorio_prospeccao") or "").strip()
            cliente.save(
                update_fields=[
                    "fase_funil",
                    "data_relatorio_prospeccao",
                    "relatorio_prospeccao",
                ]
            )
            messages.add_message(request, constants.SUCCESS, "Funil e relatório de prospecção atualizados.")
            return redirect(reverse("cliente", kwargs={"id": cliente.id}))

        tipo = request.POST.get('tipo')
        documento = request.FILES.get('documento')
        data_str = request.POST.get('data')

        if not documento:
            messages.add_message(request, constants.ERROR, "Selecione um arquivo para enviar.")
            return redirect(reverse("cliente", kwargs={"id": cliente.id}))

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
            cliente=cliente,
            tipo=tipo or "O",
            arquivo=documento,
            data_upload=data_upload,
            content=extract_document_text(documento, documento.name),
        )

        messages.add_message(request, constants.SUCCESS, "Documento enviado com sucesso.")
        return redirect(reverse('cliente', kwargs={'id': cliente.id}))


@login_required
def agenda(request):
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "criar_compromisso":
            titulo = (request.POST.get("titulo") or "").strip()
            data = (request.POST.get("data") or "").strip()
            hora_inicio = (request.POST.get("hora_inicio") or "").strip()
            hora_fim = (request.POST.get("hora_fim") or "").strip()
            descricao = (request.POST.get("descricao") or "").strip()

            if not titulo or not data or not hora_inicio or not hora_fim:
                messages.add_message(
                    request,
                    constants.ERROR,
                    "Preencha titulo, data, horario inicial e horario final.",
                )
                return redirect("agenda")

            try:
                data_hora_inicio = datetime.strptime(
                    f"{data} {hora_inicio}", "%Y-%m-%d %H:%M"
                )
                data_hora_fim = datetime.strptime(f"{data} {hora_fim}", "%Y-%m-%d %H:%M")
                if timezone.is_naive(data_hora_inicio):
                    data_hora_inicio = timezone.make_aware(data_hora_inicio)
                if timezone.is_naive(data_hora_fim):
                    data_hora_fim = timezone.make_aware(data_hora_fim)
            except ValueError:
                messages.add_message(request, constants.ERROR, "Data/hora invalida.")
                return redirect("agenda")

            # Se o horario final for menor/igual ao inicial, assume virada de dia.
            if data_hora_fim <= data_hora_inicio:
                data_hora_fim += timedelta(days=1)

            if data_hora_fim <= data_hora_inicio:
                messages.add_message(
                    request,
                    constants.ERROR,
                    "Nao foi possivel validar o intervalo do compromisso.",
                )
                return redirect("agenda")

            Compromisso.objects.create(
                user=request.user,
                titulo=titulo,
                descricao=descricao,
                data_hora=data_hora_inicio,
                data_hora_fim=data_hora_fim,
            )
            messages.add_message(request, constants.SUCCESS, "Compromisso criado com sucesso.")
            return redirect("agenda")

        if action == "criar_tarefa":
            titulo = (request.POST.get("titulo_tarefa") or "").strip()
            prazo_raw = (request.POST.get("prazo") or "").strip()

            if not titulo:
                messages.add_message(request, constants.ERROR, "Informe o titulo da tarefa.")
                return redirect("agenda")

            prazo = parse_date(prazo_raw) if prazo_raw else None
            Tarefa.objects.create(user=request.user, titulo=titulo, prazo=prazo)
            messages.add_message(request, constants.SUCCESS, "Tarefa criada com sucesso.")
            return redirect("agenda")

        if action == "toggle_tarefa":
            tarefa_id = request.POST.get("tarefa_id")
            tarefa = get_object_or_404(Tarefa, id=tarefa_id, user=request.user)
            tarefa.concluida = not tarefa.concluida
            tarefa.save(update_fields=["concluida"])
            return redirect("agenda")

        if action == "excluir_tarefa":
            tarefa_id = request.POST.get("tarefa_id")
            tarefa = get_object_or_404(Tarefa, id=tarefa_id, user=request.user)
            tarefa.delete()
            messages.add_message(request, constants.SUCCESS, "Tarefa removida.")
            return redirect("agenda")

        if action == "excluir_compromisso":
            compromisso_id = request.POST.get("compromisso_id")
            compromisso = get_object_or_404(
                Compromisso, id=compromisso_id, user=request.user
            )
            compromisso.delete()
            messages.add_message(request, constants.SUCCESS, "Compromisso removido.")
            return redirect("agenda")

    filtro_data_raw = (request.GET.get("data") or "").strip()
    filtro_data = parse_date(filtro_data_raw) if filtro_data_raw else None

    compromissos = Compromisso.objects.filter(user=request.user)
    if filtro_data:
        compromissos = compromissos.filter(data_hora__date=filtro_data)

    tarefas = Tarefa.objects.filter(user=request.user)
    status = (request.GET.get("status") or "").strip()
    if status == "pendente":
        tarefas = tarefas.filter(concluida=False)
    elif status == "concluida":
        tarefas = tarefas.filter(concluida=True)

    context = {
        "compromissos": compromissos,
        "tarefas": tarefas,
        "filtro_data": filtro_data_raw,
        "status_tarefa": status,
    }
    return render(request, "agenda.html", context)

