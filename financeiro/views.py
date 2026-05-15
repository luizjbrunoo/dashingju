from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.messages import constants
from django.db import IntegrityError
from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import BancoForm, CategoriaForm, MovimentoForm
from .models import Banco, Categoria, Movimento
from .pdf_report import gerar_relatorio_pdf


def _parse_date(s, default):
    if not s:
        return default
    from django.utils.dateparse import parse_date

    d = parse_date(s)
    return d if d else default


_MESES_PT = (
    "",
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)


def _primeiro_dia_n_meses_atras(hoje: date, n_meses: int) -> date:
    """Primeiro dia do mês que está (n_meses-1) meses antes do mês de `hoje`."""
    y, m = hoje.year, hoje.month
    for _ in range(n_meses - 1):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return date(y, m, 1)


def _ultimo_dia_mes_ref(hoje: date) -> date:
    return date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])


def _serie_historico_mensal(usuario, hoje: date, meses: int = 12):
    """Totais de receitas e despesas por mês (últimos `meses` meses)."""
    inicio = _primeiro_dia_n_meses_atras(hoje, meses)
    fim = _ultimo_dia_mes_ref(hoje)

    rec_rows = (
        Movimento.objects.filter(
            usuario=usuario,
            data__gte=inicio,
            data__lte=fim,
            categoria__tipo=Categoria.Tipo.RECEITA,
        )
        .annotate(m=TruncMonth("data"))
        .values("m")
        .annotate(total=Sum("valor"))
    )
    desp_rows = (
        Movimento.objects.filter(
            usuario=usuario,
            data__gte=inicio,
            data__lte=fim,
            categoria__tipo=Categoria.Tipo.DESPESA,
        )
        .annotate(m=TruncMonth("data"))
        .values("m")
        .annotate(total=Sum("valor"))
    )

    def _to_map(rows):
        out = {}
        for row in rows:
            tm = row["m"]
            if tm is None:
                continue
            key = (tm.year, tm.month)
            out[key] = float(row["total"] or 0)
        return out

    rec_map = _to_map(rec_rows)
    desp_map = _to_map(desp_rows)

    labels = []
    receitas = []
    despesas = []
    cy, cm = inicio.year, inicio.month
    for _ in range(meses):
        key = (cy, cm)
        labels.append(f"{_MESES_PT[cm][:3]}/{cy}")
        receitas.append(rec_map.get(key, 0.0))
        despesas.append(desp_map.get(key, 0.0))
        cm += 1
        if cm > 12:
            cm = 1
            cy += 1

    return {"labels": labels, "receitas": receitas, "despesas": despesas}


def _top_categorias_pizza(usuario, tipo: str, data_ini: date, data_fim: date, top_n: int = 7):
    rows = list(
        Movimento.objects.filter(usuario=usuario, data__gte=data_ini, data__lte=data_fim, categoria__tipo=tipo)
        .values("categoria__nome")
        .annotate(total=Sum("valor"))
        .order_by("-total")
    )
    if not rows:
        return {"labels": [], "valores": []}
    top = rows[:top_n]
    rest = rows[top_n:]
    labels = [r["categoria__nome"] for r in top]
    valores = [float(r["total"] or 0) for r in top]
    if rest:
        outros = sum(float(r["total"] or 0) for r in rest)
        if outros > 0:
            labels.append("Outros")
            valores.append(outros)
    return {"labels": labels, "valores": valores}


@login_required
def dashboard(request):
    hoje = timezone.localdate()
    inicio_mes = date(hoje.year, hoje.month, 1)
    fim_mes = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])

    bancos = Banco.objects.filter(usuario=request.user)
    mov_mes = Movimento.objects.filter(usuario=request.user, data__gte=inicio_mes, data__lte=fim_mes)

    tot_rec = (
        mov_mes.filter(categoria__tipo=Categoria.Tipo.RECEITA).aggregate(t=Sum("valor"))["t"] or Decimal("0")
    )
    tot_desp = (
        mov_mes.filter(categoria__tipo=Categoria.Tipo.DESPESA).aggregate(t=Sum("valor"))["t"] or Decimal("0")
    )

    saldos = [{"banco": b.nome, "saldo": b.saldo_atual()} for b in bancos]

    inicio_12 = _primeiro_dia_n_meses_atras(hoje, 12)
    fim_12 = _ultimo_dia_mes_ref(hoje)
    historico_mensal = _serie_historico_mensal(request.user, hoje, 12)
    pizza_receitas = _top_categorias_pizza(request.user, Categoria.Tipo.RECEITA, inicio_12, fim_12)
    pizza_despesas = _top_categorias_pizza(request.user, Categoria.Tipo.DESPESA, inicio_12, fim_12)

    charts_payload = {
        "historico": historico_mensal,
        "pizzaReceitas": pizza_receitas,
        "pizzaDespesas": pizza_despesas,
    }

    return render(
        request,
        "financeiro/dashboard.html",
        {
            "bancos": bancos,
            "saldos": saldos,
            "total_receitas_mes": tot_rec,
            "total_despesas_mes": tot_desp,
            "saldo_mes": tot_rec - tot_desp,
            "mes_ref": f"{_MESES_PT[hoje.month]}/{hoje.year}",
            "charts_payload": charts_payload,
            "periodo_graficos_inicio": inicio_12,
            "periodo_graficos_fim": fim_12,
        },
    )


@login_required
def banco_listar(request):
    bancos = Banco.objects.filter(usuario=request.user)
    return render(request, "financeiro/banco_listar.html", {"bancos": bancos})


@login_required
def banco_novo(request):
    if request.method == "POST":
        form = BancoForm(request.POST)
        if form.is_valid():
            b = form.save(commit=False)
            b.usuario = request.user
            b.save()
            messages.add_message(request, constants.SUCCESS, "Banco cadastrado.")
            return redirect("financeiro_banco_listar")
    else:
        form = BancoForm()
    return render(request, "financeiro/banco_form.html", {"form": form, "titulo": "Novo banco"})


@login_required
def banco_editar(request, pk):
    banco = get_object_or_404(Banco, pk=pk, usuario=request.user)
    if request.method == "POST":
        form = BancoForm(request.POST, instance=banco)
        if form.is_valid():
            form.save()
            messages.add_message(request, constants.SUCCESS, "Banco atualizado.")
            return redirect("financeiro_banco_listar")
    else:
        form = BancoForm(instance=banco)
    return render(request, "financeiro/banco_form.html", {"form": form, "titulo": "Editar banco"})


@login_required
def banco_excluir(request, pk):
    banco = get_object_or_404(Banco, pk=pk, usuario=request.user)
    if request.method == "POST":
        banco.delete()
        messages.add_message(request, constants.SUCCESS, "Banco removido.")
        return redirect("financeiro_banco_listar")
    return render(request, "financeiro/banco_confirmar_exclusao.html", {"banco": banco})


@login_required
def categoria_listar(request):
    categorias = Categoria.objects.filter(usuario=request.user)
    return render(request, "financeiro/categoria_listar.html", {"categorias": categorias})


@login_required
def categoria_nova(request):
    if request.method == "POST":
        form = CategoriaForm(request.POST)
        if form.is_valid():
            c = form.save(commit=False)
            c.usuario = request.user
            try:
                c.save()
            except IntegrityError:
                messages.add_message(
                    request,
                    constants.ERROR,
                    "Não foi possível salvar (nome duplicado para o mesmo tipo?).",
                )
                return redirect("financeiro_categoria_nova")
            messages.add_message(request, constants.SUCCESS, "Categoria cadastrada.")
            return redirect("financeiro_categoria_listar")
    else:
        form = CategoriaForm()
    return render(request, "financeiro/categoria_form.html", {"form": form, "titulo": "Nova categoria"})


@login_required
def categoria_editar(request, pk):
    cat = get_object_or_404(Categoria, pk=pk, usuario=request.user)
    if request.method == "POST":
        form = CategoriaForm(request.POST, instance=cat)
        if form.is_valid():
            form.save()
            messages.add_message(request, constants.SUCCESS, "Categoria atualizada.")
            return redirect("financeiro_categoria_listar")
    else:
        form = CategoriaForm(instance=cat)
    return render(request, "financeiro/categoria_form.html", {"form": form, "titulo": "Editar categoria"})


@login_required
def categoria_excluir(request, pk):
    cat = get_object_or_404(Categoria, pk=pk, usuario=request.user)
    if request.method == "POST":
        try:
            cat.delete()
        except ProtectedError:
            messages.add_message(
                request,
                constants.ERROR,
                "Não é possível excluir: existem lançamentos usando esta categoria.",
            )
            return redirect("financeiro_categoria_listar")
        messages.add_message(request, constants.SUCCESS, "Categoria removida.")
        return redirect("financeiro_categoria_listar")
    return render(request, "financeiro/categoria_confirmar_exclusao.html", {"categoria": cat})


def _extrato_queryset(request):
    hoje = timezone.localdate()
    inicio_padrao = date(hoje.year, hoje.month, 1)
    fim_padrao = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])

    data_inicio = _parse_date(request.GET.get("data_inicio"), inicio_padrao)
    data_fim = _parse_date(request.GET.get("data_fim"), fim_padrao)
    if data_fim < data_inicio:
        data_fim = data_inicio

    qs = Movimento.objects.filter(usuario=request.user, data__gte=data_inicio, data__lte=data_fim).select_related(
        "banco", "categoria"
    )

    banco_id = request.GET.get("banco")
    if banco_id:
        qs = qs.filter(banco_id=banco_id)

    tipo = request.GET.get("tipo") or "todos"
    if tipo == "receita":
        qs = qs.filter(categoria__tipo=Categoria.Tipo.RECEITA)
    elif tipo == "despesa":
        qs = qs.filter(categoria__tipo=Categoria.Tipo.DESPESA)

    return qs, data_inicio, data_fim, tipo, banco_id


def _categorias_json_para_formulario(usuario):
    """Receitas e despesas separadas para atualizar o select de categoria ao mudar o tipo."""
    data = {"receita": [], "despesa": []}
    for c in Categoria.objects.filter(usuario=usuario).order_by("nome"):
        data[c.tipo].append({"id": c.pk, "nome": c.nome})
    return data


@login_required
def extrato(request):
    qs, data_inicio, data_fim, tipo, banco_id = _extrato_queryset(request)

    tot_rec = (
        qs.filter(categoria__tipo=Categoria.Tipo.RECEITA).aggregate(t=Sum("valor"))["t"] or Decimal("0")
    )
    tot_desp = (
        qs.filter(categoria__tipo=Categoria.Tipo.DESPESA).aggregate(t=Sum("valor"))["t"] or Decimal("0")
    )

    bancos_opts = Banco.objects.filter(usuario=request.user)

    return render(
        request,
        "financeiro/extrato.html",
        {
            "movimentos": qs,
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "tipo": tipo,
            "banco_id": banco_id or "",
            "bancos_opts": bancos_opts,
            "total_receitas": tot_rec,
            "total_despesas": tot_desp,
            "saldo_periodo": tot_rec - tot_desp,
        },
    )


@login_required
def movimento_novo(request):
    if request.method == "POST":
        form = MovimentoForm(request.POST, usuario=request.user)
        if form.is_valid():
            m = form.save(commit=False)
            m.usuario = request.user
            m.save()
            messages.add_message(request, constants.SUCCESS, "Lançamento registrado.")
            return redirect("financeiro_extrato")
    else:
        form = MovimentoForm(usuario=request.user)
    return render(
        request,
        "financeiro/movimento_form.html",
        {
            "form": form,
            "titulo": "Novo lançamento",
            "categorias_json": _categorias_json_para_formulario(request.user),
        },
    )


@login_required
def movimento_editar(request, pk):
    mov = get_object_or_404(Movimento, pk=pk, usuario=request.user)
    if request.method == "POST":
        form = MovimentoForm(request.POST, instance=mov, usuario=request.user)
        if form.is_valid():
            m = form.save(commit=False)
            m.usuario = request.user
            m.save()
            messages.add_message(request, constants.SUCCESS, "Lançamento atualizado.")
            return redirect("financeiro_extrato")
    else:
        form = MovimentoForm(instance=mov, usuario=request.user)
    return render(
        request,
        "financeiro/movimento_form.html",
        {
            "form": form,
            "titulo": "Editar lançamento",
            "categorias_json": _categorias_json_para_formulario(request.user),
        },
    )


@login_required
def movimento_excluir(request, pk):
    mov = get_object_or_404(Movimento, pk=pk, usuario=request.user)
    if request.method == "POST":
        mov.delete()
        messages.add_message(request, constants.SUCCESS, "Lançamento excluído.")
        return redirect("financeiro_extrato")
    return render(request, "financeiro/movimento_confirmar_exclusao.html", {"movimento": mov})


@login_required
def relatorio_pdf(request):
    qs, data_inicio, data_fim, tipo, _banco_id = _extrato_queryset(request)

    tot_rec = (
        qs.filter(categoria__tipo=Categoria.Tipo.RECEITA).aggregate(t=Sum("valor"))["t"] or Decimal("0")
    )
    tot_desp = (
        qs.filter(categoria__tipo=Categoria.Tipo.DESPESA).aggregate(t=Sum("valor"))["t"] or Decimal("0")
    )

    linhas = []
    for m in qs.order_by("data", "id"):
        linhas.append(
            {
                "data": m.data.strftime("%d/%m/%Y"),
                "banco": m.banco.nome,
                "categoria": m.categoria.nome,
                "tipo": m.categoria.get_tipo_display(),
                "valor": m.valor,
                "descricao": m.descricao,
            }
        )

    bancos = Banco.objects.filter(usuario=request.user)
    saldos_por_banco = [{"banco": b.nome, "saldo": b.saldo_atual()} for b in bancos]

    periodo_texto = f"{data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
    if tipo != "todos":
        periodo_texto += f" ({tipo})"

    pdf_bytes = gerar_relatorio_pdf(
        titulo_usuario=request.user.username,
        periodo_texto=periodo_texto,
        linhas=linhas,
        totais={
            "total_receitas": tot_rec,
            "total_despesas": tot_desp,
            "saldo_periodo": tot_rec - tot_desp,
        },
        saldos_por_banco=saldos_por_banco,
    )

    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = 'attachment; filename="relatorio_financeiro.pdf"'
    return resp
