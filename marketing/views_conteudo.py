from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from financeiro.tenancy_write import organization_for_finance_write
from marketing.decorators import (
    login_e_perm_conteudo,
    login_e_perm_edit_conteudo,
)
from marketing.permissions import (
    pode_editar_conteudo_marketing,
    pode_ver_metricas_financeiras_marketing,
    pode_ver_resultados_marketing,
)
from marketing.services.marketing_pro import montar_marketing_pro
from marketing.services.periodo import PeriodoMarketing

from .choices import AREAS_JURIDICAS_PADRAO, PlataformaMarketing, StatusConteudo, StatusIdeia
from .forms import (
    BancoIdeiasForm,
    BibliotecaFiltroForm,
    ContentItemForm,
    ContentProfileForm,
    IdeiaAgendarForm,
    PlanoEditorialForm,
    ReaproveitarForm,
)
from .models import ContentIdea, ContentItem, ContentProfile, EditorialCalendar
from .services.calendario_ia import PlanoEditorialService
from .services.compliance import AVISO_LEGAL, ComplianceService
from .services.analytics import (
    metricas_agregadas,
    plataformas_com_status,
    solicitar_integracao,
    tem_dados_reais,
    tem_integracao_ativa,
    top_conteudos,
)
from .services.conteudo_ia import ContentGenerationError, ContentGenerationService
from .services.ideias_ia import BancoIdeiasService
from .services.reaproveitamento_ia import ReaproveitamentoService

MENSAGENS_ACAO = {
    "gerar_ia": "Conteúdo gerado por IA. Status: Gerado por IA — revisão necessária.",
    "regenerar": "Nova versão gerada.",
    "melhorar": "Conteúdo melhorado pela IA.",
    "encurtar": "Versão encurtada gerada.",
    "expandir": "Versão expandida gerada.",
    "alterar_tom": "Tom alterado pela IA.",
    "enviar_revisao": "Conteúdo enviado para revisão.",
    "aprovar": "Conteúdo aprovado.",
    "verificar_conteudo": "Verificação automatizada concluída.",
}


def _org(request):
    return organization_for_finance_write(request)


def _itens_org(organization):
    if organization is None:
        return ContentItem.objects.none()
    return ContentItem.objects.filter(organization=organization)


def _ideias_org(organization):
    if organization is None:
        return ContentIdea.objects.none()
    return ContentIdea.objects.filter(organization=organization)


def _perfil_org(organization, user) -> ContentProfile | None:
    if organization is None:
        return None
    profile = ContentProfile.objects.filter(organization=organization).first()
    if profile is not None:
        return profile
    return ContentProfile.objects.create(organization=organization, usuario=user)


def _areas_do_perfil(user, organization=None) -> list[str]:
    profile = _perfil_org(organization, user)
    if profile is None:
        return AREAS_JURIDICAS_PADRAO
    areas = profile.areas_lista()
    return areas if areas else AREAS_JURIDICAS_PADRAO


def _kpis_conteudo(organization) -> dict[str, int]:
    hoje = timezone.localdate()
    inicio_mes = hoje.replace(day=1)
    base = _itens_org(organization)
    return {
        "criados_mes": base.filter(criado_em__date__gte=inicio_mes).count(),
        "em_planejamento": base.filter(
            status__in=[StatusConteudo.IDEIA, StatusConteudo.RASCUNHO]
        ).count(),
        "aguardando_revisao": base.filter(
            status__in=[StatusConteudo.GERADO_IA, StatusConteudo.EM_REVISAO]
        ).count(),
        "aprovados": base.filter(
            status__in=[StatusConteudo.APROVADO, StatusConteudo.AGENDADO]
        ).count(),
        "publicados": base.filter(status=StatusConteudo.PUBLICADO).count(),
    }


def _calendario_itens(organization, dias: int = 42):
    hoje = timezone.localdate()
    fim = hoje + timedelta(days=dias)
    return list(
        _itens_org(organization)
        .filter(data_planejada__gte=hoje, data_planejada__lte=fim)
        .order_by("data_planejada", "titulo")
    )


def _item_or_404(request, pk):
    organization = _org(request)
    if organization is None:
        raise Http404()
    return get_object_or_404(ContentItem, pk=pk, organization=organization)


def _ideia_or_404(request, pk):
    organization = _org(request)
    if organization is None:
        raise Http404()
    return get_object_or_404(ContentIdea, pk=pk, organization=organization)


def _processar_form_conteudo(request, form, *, item=None):
    acao = request.POST.get("acao", "salvar")
    organization = _org(request)
    profile = _perfil_org(organization, request.user)
    service = ContentGenerationService(profile=profile)

    if acao in ContentGenerationService.ACOES_IA:
        if not form.is_valid():
            return None
        if organization is None:
            messages.error(
                request,
                "Não foi possível determinar o escritório ativo para esta operação.",
            )
            return redirect("marketing_conteudo_dashboard")
        if item is None:
            item = form.save(commit=False)
            item.usuario = request.user
            item.organization = organization
            item.responsavel = request.user
            item.save()
        else:
            item = form.save()
        conteudo_origem = form.cleaned_data.get("conteudo_origem")
        try:
            service.gerar_e_salvar(
                item,
                user=request.user,
                acao=acao,
                conteudo_origem=conteudo_origem,
            )
        except ContentGenerationError as exc:
            messages.error(request, str(exc))
            return redirect("marketing_conteudo_editar", pk=item.pk)
        messages.success(request, MENSAGENS_ACAO.get(acao, "Conteúdo atualizado pela IA."))
        return redirect("marketing_conteudo_editar", pk=item.pk)

    if acao == "enviar_revisao":
        if form.is_valid():
            item = form.save()
            item.status = StatusConteudo.EM_REVISAO
            item.save(update_fields=["status", "atualizado_em"])
            messages.success(request, MENSAGENS_ACAO["enviar_revisao"])
            return redirect("marketing_conteudo_editar", pk=item.pk)
        return None

    if acao == "aprovar":
        if form.is_valid():
            item = form.save()
            item.status = StatusConteudo.APROVADO
            item.save(update_fields=["status", "atualizado_em"])
            messages.success(request, MENSAGENS_ACAO["aprovar"])
            return redirect("marketing_conteudo_editar", pk=item.pk)
        return None

    if acao == "verificar_conteudo":
        if not item:
            return None
        if form.is_valid():
            item = form.save()
        ultima_versao = item.versoes.order_by("-numero").first()
        try:
            ComplianceService().verificar(
                item, user=request.user, versao=ultima_versao
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("marketing_conteudo_editar", pk=item.pk)
        if item.status == StatusConteudo.GERADO_IA:
            item.status = StatusConteudo.EM_REVISAO
            item.save(update_fields=["status", "atualizado_em"])
        messages.success(request, AVISO_LEGAL)
        return redirect("marketing_conteudo_editar", pk=item.pk)

    if form.is_valid():
        saved = form.save(commit=False)
        if item is None:
            organization = _org(request)
            if organization is None:
                messages.error(
                    request,
                    "Não foi possível determinar o escritório ativo para esta operação.",
                )
                return redirect("marketing_conteudo_dashboard")
            saved.usuario = request.user
            saved.organization = organization
            saved.responsavel = request.user
        saved.save()
        messages.success(request, "Conteúdo salvo.")
        return redirect("marketing_conteudo_editar", pk=saved.pk)
    return None


@login_e_perm_conteudo
def dashboard(request):
    return render(
        request,
        "marketing/conteudo/dashboard.html",
        {
            "kpis": _kpis_conteudo(_org(request)),
            "calendario_itens": _calendario_itens(_org(request)),
            "ideias_pendentes": _ideias_org(_org(request))
            .filter(status=StatusIdeia.PENDENTE)
            .count(),
            "subnav_section": "conteudo",
        },
    )


@login_e_perm_edit_conteudo
def perfil(request):
    profile = _perfil_org(_org(request), request.user)
    if request.method == "POST":
        if profile is None:
            messages.error(
                request,
                "Não foi possível determinar o escritório ativo para esta operação.",
            )
            return redirect("marketing_conteudo_dashboard")
        form = ContentProfileForm(request.POST, instance=profile)
        if form.is_valid():
            inst = form.save(commit=False)
            inst.usuario = request.user
            inst.organization = _org(request)
            inst.save()
            messages.success(request, "Perfil de conteúdo salvo.")
            return redirect("marketing_conteudo_perfil")
    else:
        form = ContentProfileForm(instance=profile)
    return render(
        request,
        "marketing/conteudo/perfil.html",
        {"form": form, "subnav_section": "conteudo"},
    )


@login_e_perm_conteudo
def biblioteca(request):
    form = BibliotecaFiltroForm(request.GET or None)
    qs = _itens_org(_org(request)).select_related("responsavel")
    if form.is_valid():
        if form.cleaned_data.get("canal"):
            qs = qs.filter(canal=form.cleaned_data["canal"])
        if form.cleaned_data.get("area_juridica"):
            qs = qs.filter(area_juridica__icontains=form.cleaned_data["area_juridica"])
        if form.cleaned_data.get("formato"):
            qs = qs.filter(formato__icontains=form.cleaned_data["formato"])
        if form.cleaned_data.get("status"):
            qs = qs.filter(status=form.cleaned_data["status"])
        if form.cleaned_data.get("q"):
            q = form.cleaned_data["q"]
            qs = qs.filter(Q(titulo__icontains=q) | Q(tema__icontains=q))
    return render(
        request,
        "marketing/conteudo/biblioteca.html",
        {
            "form": form,
            "itens": qs[:200],
            "subnav_section": "conteudo",
        },
    )


@login_e_perm_edit_conteudo
def criar(request):
    areas = _areas_do_perfil(request.user, _org(request))
    if request.method == "POST":
        form = ContentItemForm(
            request.POST,
            areas_juridicas=areas,
            usuario=request.user,
            organization=_org(request),
        )
        redirect_resp = _processar_form_conteudo(request, form)
        if redirect_resp:
            return redirect_resp
    else:
        form = ContentItemForm(
            areas_juridicas=areas,
            usuario=request.user,
            organization=_org(request),
        )
    return render(
        request,
        "marketing/conteudo/form.html",
        {
            "form": form,
            "titulo_pagina": "Criar conteúdo",
            "subnav_section": "conteudo",
        },
    )


@login_e_perm_edit_conteudo
def editar(request, pk: int):
    item = _item_or_404(request, pk)
    areas = _areas_do_perfil(request.user, _org(request))
    if request.method == "POST":
        form = ContentItemForm(
            request.POST,
            instance=item,
            areas_juridicas=areas,
            usuario=request.user,
            organization=_org(request),
        )
        redirect_resp = _processar_form_conteudo(request, form, item=item)
        if redirect_resp:
            return redirect_resp
    else:
        form = ContentItemForm(
            instance=item,
            areas_juridicas=areas,
            usuario=request.user,
            organization=_org(request),
        )
    ultima_versao = item.versoes.order_by("-numero").first()
    ultima_verificacao = item.aprovacoes.order_by("-revisado_em").first()
    return render(
        request,
        "marketing/conteudo/form.html",
        {
            "form": form,
            "item": item,
            "ultima_versao": ultima_versao,
            "ultima_verificacao": ultima_verificacao,
            "titulo_pagina": "Editar conteúdo",
            "subnav_section": "conteudo",
        },
    )


@login_e_perm_conteudo
def analytics(request):
    user = request.user
    organization = _org(request)
    metricas = metricas_agregadas(user, organization=organization)
    tem_dados = tem_dados_reais(user, organization=organization)
    tem_integracao = tem_integracao_ativa(user, organization=organization)
    mkt_pro = None
    if pode_ver_resultados_marketing(user):
        mkt_pro = montar_marketing_pro(
            user,
            PeriodoMarketing.ultimos_dias(30),
            organization=organization_for_finance_write(request),
            ocultar_financeiro=not pode_ver_metricas_financeiras_marketing(user),
        )
    return render(
        request,
        "marketing/conteudo/analytics.html",
        {
            "metricas": metricas,
            "top_conteudos": top_conteudos(user, organization=organization) if tem_dados else [],
            "tem_dados": tem_dados,
            "tem_integracao": tem_integracao,
            "plataformas": plataformas_com_status(user, organization=organization),
            "subnav_section": "conteudo",
            "mkt_pro": mkt_pro,
        },
    )


@login_e_perm_edit_conteudo
def integracoes(request):
    if request.method == "POST":
        plataforma = request.POST.get("plataforma")
        organization = _org(request)
        if plataforma in dict(PlataformaMarketing.choices):
            if organization is None:
                messages.error(
                    request,
                    "Não foi possível determinar o escritório ativo para esta operação.",
                )
                return redirect("marketing_conteudo_integracoes")
            solicitar_integracao(request.user, plataforma, organization=organization)
            messages.info(
                request,
                "Solicitação registrada. A sincronização com plataformas estará disponível em breve.",
            )
            return redirect("marketing_conteudo_integracoes")
    return render(
        request,
        "marketing/conteudo/integracoes.html",
        {
            "plataformas": plataformas_com_status(request.user, organization=_org(request)),
            "subnav_section": "conteudo",
        },
    )


@login_e_perm_edit_conteudo
def calendario_plano(request):
    areas = _areas_do_perfil(request.user, _org(request))
    profile = _perfil_org(_org(request), request.user)
    if request.method == "POST":
        form = PlanoEditorialForm(request.POST)
        if form.is_valid():
            service = PlanoEditorialService(profile=profile)
            try:
                calendario = service.gerar_plano(
                    user=request.user,
                    organization=_org(request),
                    parametros=form.cleaned_data,
                )
            except ContentGenerationError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f"Plano editorial criado com {calendario.itens.count()} conteúdos planejados.",
                )
                return redirect("marketing_conteudo_dashboard")
    else:
        initial = {}
        if profile and profile.publico:
            initial["publico"] = profile.publico
        if areas:
            initial["area_juridica"] = areas[0]
        form = PlanoEditorialForm(initial=initial)
    planos = (
        EditorialCalendar.objects.filter(organization=_org(request))[:10]
        if _org(request) is not None
        else EditorialCalendar.objects.none()
    )
    return render(
        request,
        "marketing/conteudo/calendario_plano.html",
        {
            "form": form,
            "planos": planos,
            "subnav_section": "conteudo",
        },
    )


@login_e_perm_conteudo
def ideias(request):
    areas = _areas_do_perfil(request.user, _org(request))
    profile = _perfil_org(_org(request), request.user)
    if request.method == "POST" and request.POST.get("acao") == "sugerir":
        if not pode_editar_conteudo_marketing(request.user):
            messages.error(request, "Sem permissão para gerar ideias com IA.")
            return redirect("marketing_conteudo_ideias")
        form = BancoIdeiasForm(request.POST, areas_juridicas=areas)
        if form.is_valid():
            service = BancoIdeiasService(profile=profile)
            try:
                criadas = service.sugerir(
                    user=request.user,
                    organization=_org(request),
                    parametros=form.cleaned_data,
                )
            except ContentGenerationError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"{len(criadas)} ideias sugeridas pela IA.")
                return redirect("marketing_conteudo_ideias")
    else:
        initial = {}
        if profile and profile.publico:
            initial["publico"] = profile.publico
        form = BancoIdeiasForm(initial=initial, areas_juridicas=areas)

    ideias_lista = _ideias_org(_org(request)).filter(status=StatusIdeia.PENDENTE)[:50]
    return render(
        request,
        "marketing/conteudo/ideias.html",
        {
            "form": form,
            "ideias": ideias_lista,
            "subnav_section": "conteudo",
        },
    )


@login_e_perm_edit_conteudo
def ideia_criar_conteudo(request, pk: int):
    if request.method != "POST":
        return redirect("marketing_conteudo_ideias")
    ideia = _ideia_or_404(request, pk)
    organization = _org(request)
    item = ContentItem.objects.create(
        usuario=request.user,
        organization=organization,
        titulo=ideia.titulo,
        tema=ideia.descricao[:255] if ideia.descricao else ideia.titulo,
        area_juridica=ideia.area_juridica,
        canal=ideia.canal_sugerido or "blog",
        objetivo=ideia.objetivo,
        status=StatusConteudo.RASCUNHO,
        responsavel=request.user,
    )
    ideia.status = StatusIdeia.USADA
    ideia.save(update_fields=["status"])
    messages.success(request, "Conteúdo criado a partir da ideia.")
    return redirect("marketing_conteudo_editar", pk=item.pk)


@login_e_perm_edit_conteudo
def ideia_agendar(request, pk: int):
    ideia = _ideia_or_404(request, pk)
    if request.method == "POST":
        form = IdeiaAgendarForm(request.POST)
        if form.is_valid():
            item = ContentItem.objects.create(
                usuario=request.user,
                organization=_org(request),
                titulo=ideia.titulo,
                tema=ideia.descricao[:255] if ideia.descricao else ideia.titulo,
                area_juridica=ideia.area_juridica,
                canal=ideia.canal_sugerido or "blog",
                objetivo=ideia.objetivo,
                status=StatusConteudo.IDEIA,
                data_planejada=form.cleaned_data["data_planejada"],
                responsavel=request.user,
            )
            ideia.status = StatusIdeia.USADA
            ideia.save(update_fields=["status"])
            messages.success(request, "Ideia adicionada ao calendário.")
            return redirect("marketing_conteudo_dashboard")
    else:
        form = IdeiaAgendarForm(
            initial={"data_planejada": timezone.localdate() + timedelta(days=1)}
        )
    return render(
        request,
        "marketing/conteudo/ideia_agendar.html",
        {"form": form, "ideia": ideia, "subnav_section": "conteudo"},
    )


@login_e_perm_edit_conteudo
def ideia_descartar(request, pk: int):
    if request.method != "POST":
        return redirect("marketing_conteudo_ideias")
    ideia = _ideia_or_404(request, pk)
    ideia.status = StatusIdeia.DESCARTADA
    ideia.save(update_fields=["status"])
    messages.success(request, "Ideia descartada.")
    return redirect("marketing_conteudo_ideias")


@login_e_perm_edit_conteudo
def reaproveitar(request, pk: int):
    origem = _item_or_404(request, pk)
    derivados = origem.derivados.order_by("-criado_em")[:20]
    if request.method == "POST":
        form = ReaproveitarForm(request.POST)
        if form.is_valid():
            profile = _perfil_org(_org(request), request.user)
            service = ReaproveitamentoService(profile=profile)
            try:
                criados = service.reaproveitar(
                    origem,
                    form.cleaned_data["formatos"],
                    user=request.user,
                )
            except ContentGenerationError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f"{len(criados)} conteúdo(s) gerado(s) a partir de «{origem.titulo}».",
                )
                return redirect("marketing_conteudo_biblioteca")
    else:
        form = ReaproveitarForm()
    return render(
        request,
        "marketing/conteudo/reaproveitar.html",
        {
            "form": form,
            "origem": origem,
            "derivados": derivados,
            "subnav_section": "conteudo",
        },
    )
