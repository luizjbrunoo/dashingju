"""Seed DEMO do Comercial PRO — fictício, isolado, idempotente, sem calls externos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from comercial.models import MetaComercial
from comercial.services.pipeline import formatar_motivo_perda
from financeiro.choices import CategoriaCobranca, StatusCobranca, StatusContrato
from financeiro.models import Cobranca, CobrancaRecebimento, Contrato
from organizacoes.models import Membership, Organization
from usuarios.choices import OrigemLead, StatusCompromisso, TipoCompromisso
from usuarios.models import Cliente, Compromisso

DEMO_SLUG = "almeida-torres-advocacia-demo"
DEMO_NAME = "Almeida & Torres Advocacia — DEMO"
UTM_CONTENT = "comercial_pro_02"
EMAIL_SUFFIX = "@comercial-pro.demo"
TICKET_DEMO = Decimal("15000.00")
META_MENSAL_DEMO = Decimal("100000.00")

ERR_ORG_NOT_FOUND = "DEMO_ORGANIZATION_NOT_FOUND"
ERR_NO_MEMBER = "DEMO_MEMBERSHIP_NOT_FOUND"


@dataclass(frozen=True)
class ResumoSeedComercial:
    organization_id: int
    clientes: int
    compromissos: int
    contratos: int
    perdas: int
    ganhos: int
    meta_preservada: bool


def resolver_organization_demo() -> Organization | None:
    org = Organization.objects.filter(slug=DEMO_SLUG).first()
    if org:
        return org
    return Organization.objects.filter(name=DEMO_NAME).first()


def _actor(org: Organization):
    qs = Membership.objects.filter(
        organization=org, status=Membership.Status.ACTIVE
    ).select_related("user")
    owner = qs.filter(role=Membership.Role.OWNER).first()
    member = owner or qs.first()
    return member.user if member else None


def _aware(dia: date, hora=10):
    return timezone.make_aware(datetime.combine(dia, time(hour=hora)))


def _limpar(org: Organization) -> None:
    marcados = Cliente.objects.filter(organization=org, utm_content=UTM_CONTENT)
    ids = list(marcados.values_list("pk", flat=True))
    if not ids:
        return
    CobrancaRecebimento.objects.filter(organization=org, cobranca__cliente_id__in=ids).delete()
    Cobranca.objects.filter(organization=org, cliente_id__in=ids).delete()
    Contrato.objects.filter(organization=org, cliente_id__in=ids).delete()
    Compromisso.objects.filter(cliente_id__in=ids).delete()
    marcados.delete()


def _cliente(user, org, *, nome, email, status, fase, criado, motivo="", origem=OrigemLead.INDICACAO):
    rel = formatar_motivo_perda(motivo) if motivo else ""
    cli = Cliente.objects.create(
        user=user,
        organization=org,
        nome=nome,
        email=email,
        status=status,
        fase_funil=fase,
        origem=origem,
        utm_content=UTM_CONTENT,
        relatorio_prospeccao=rel,
        telefone="11900000000",
    )
    Cliente.objects.filter(pk=cli.pk).update(criado_em=_aware(criado, 9))
    cli.refresh_from_db()
    return cli


def _compromisso(user, org, cliente, *, titulo, tipo, status, quando):
    return Compromisso.objects.create(
        user=user,
        organization=org,
        cliente=cliente,
        titulo=titulo,
        tipo=tipo,
        status=status,
        data_hora=_aware(quando) if isinstance(quando, date) else quando,
        responsavel=user,
    )


def popular_comercial_demo(*, executar: bool = True) -> ResumoSeedComercial:
    org = resolver_organization_demo()
    if org is None:
        raise ValueError(ERR_ORG_NOT_FOUND)
    user = _actor(org)
    if user is None:
        raise ValueError(ERR_NO_MEMBER)
    if not executar:
        return ResumoSeedComercial(
            organization_id=org.pk,
            clientes=0,
            compromissos=0,
            contratos=0,
            perdas=0,
            ganhos=0,
            meta_preservada=MetaComercial.objects.filter(
                organization=org, ano=timezone.localdate().year
            ).exists(),
        )

    hoje = timezone.localdate()
    inicio = hoje.replace(day=1)
    d20 = hoje - timedelta(days=20)
    d10 = hoje - timedelta(days=10)
    d3 = hoje - timedelta(days=3)
    futuro = hoje + timedelta(days=3)
    ano = hoje.year

    with transaction.atomic():
        _limpar(org)

        meta_existia = MetaComercial.objects.filter(organization=org, ano=ano).exists()
        if not meta_existia:
            MetaComercial.objects.create(
                organization=org,
                usuario=user,
                ano=ano,
                meta_anual=META_MENSAL_DEMO * 12,
                meta_mensal=META_MENSAL_DEMO,
                ticket_medio=TICKET_DEMO,
                ticket_medio_manual=True,
                vigencia_inicio=date(ano, 1, 1),
                vigencia_fim=date(ano, 12, 31),
                observacoes="Meta fictícia do tenant DEMO — Comercial PRO.",
                criado_por=user,
            )

        slots = [
            ("Prospect Frio Alfa", "frio.alfa", "em_prospeccao", "primeiro_contato", d20, "", None),
            ("Prospect Frio Beta", "frio.beta", "em_prospeccao", "novo_contato", d20, "", None),
            ("Prospect Morno Alfa", "morno.alfa", "em_prospeccao", "primeiro_contato", d10, "consulta", None),
            ("Prospect Morno Beta", "morno.beta", "em_prospeccao", "primeiro_contato", d10, "consulta", None),
            ("Prospect Quente Alfa", "quente.alfa", "em_prospeccao", "proposta_enviada", d10, "recente", None),
            ("Prospect Quente Beta", "quente.beta", "em_prospeccao", "proposta_enviada", d10, "recente", None),
            ("Prospect Quente Gama", "quente.gama", "em_prospeccao", "proposta_enviada", d10, "recente", None),
            ("Prospect Quente Delta", "quente.delta", "em_prospeccao", "proposta_enviada", d10, "recente", None),
            ("Prospect Quente Epsilon", "quente.eps", "em_prospeccao", "proposta_enviada", d10, "recente", None),
            ("Prospect Fervendo Alfa", "ferv.alfa", "em_prospeccao", "aguardando_decisao", d10, "fervendo", None),
            ("Prospect Fervendo Beta", "ferv.beta", "em_prospeccao", "aguardando_decisao", d10, "fervendo", None),
            ("Prospect Novo Alfa", "novo.alfa", "em_prospeccao", "primeiro_contato", hoje, "", None),
            ("Prospect Novo Beta", "novo.beta", "em_prospeccao", "primeiro_contato", hoje, "", None),
            ("Cliente Fechado Alfa", "ganho.alfa", "ativo", "aguardando_decisao", inicio, "ganho", Decimal("12000.00")),
            ("Cliente Fechado Beta", "ganho.beta", "ativo", "aguardando_decisao", inicio, "ganho", Decimal("15000.00")),
            ("Cliente Fechado Gama", "ganho.gama", "ativo", "aguardando_decisao", inicio, "ganho", Decimal("18000.00")),
            ("Perda Preço Alfa", "perda.preco", "inativo", "proposta_enviada", inicio, "honorarios", None),
            ("Perda Sem Retorno", "perda.retorno", "inativo", "proposta_enviada", inicio, "sem_retorno", None),
            ("Perda Outro Escritório", "perda.outroesc", "inativo", "aguardando_decisao", inicio, "outro_escritorio", None),
            ("Perda Desistiu", "perda.desistiu", "inativo", "primeiro_contato", inicio, "desistiu", None),
            ("Perda Atendimento", "perda.atend", "inativo", "proposta_enviada", inicio, "atendimento", None),
        ]

        criados = []
        for nome, slug, status, fase, criado, sinal, valor in slots:
            motivo = sinal if sinal in {
                "honorarios",
                "sem_retorno",
                "outro_escritorio",
                "desistiu",
                "atendimento",
            } else ""
            cli = _cliente(
                user,
                org,
                nome=nome,
                email=f"{slug}{EMAIL_SUFFIX}",
                status=status,
                fase=fase,
                criado=criado,
                motivo=motivo,
            )
            criados.append((cli, sinal, valor))

        comps = 0
        contratos = 0
        for cli, sinal, valor in criados:
            if sinal == "consulta":
                _compromisso(
                    user, org, cli,
                    titulo="Consulta inicial (DEMO)",
                    tipo=TipoCompromisso.CONSULTA,
                    status=StatusCompromisso.REALIZADO,
                    quando=d3,
                )
                comps += 1
            elif sinal == "recente":
                _compromisso(
                    user, org, cli,
                    titulo="Reunião de proposta (DEMO)",
                    tipo=TipoCompromisso.REUNIAO,
                    status=StatusCompromisso.REALIZADO,
                    quando=d3,
                )
                comps += 1
            elif sinal == "fervendo":
                _compromisso(
                    user, org, cli,
                    titulo="Reunião de fechamento (DEMO)",
                    tipo=TipoCompromisso.REUNIAO,
                    status=StatusCompromisso.REALIZADO,
                    quando=d3,
                )
                _compromisso(
                    user, org, cli,
                    titulo="Follow-up comercial (DEMO)",
                    tipo=TipoCompromisso.FOLLOWUP_COMERCIAL,
                    status=StatusCompromisso.AGENDADO,
                    quando=futuro,
                )
                comps += 2
            elif sinal == "ganho" and valor:
                Contrato.objects.create(
                    usuario=user,
                    organization=org,
                    cliente=cli,
                    referencia=f"DEMO-CPRO-{cli.pk}",
                    descricao="Contrato fictício DEMO",
                    valor_total=valor,
                    status=StatusContrato.ACTIVE,
                    criado_por=user,
                    responsavel=user,
                )
                contratos += 1

        ganho_alfa = next(c for c, s, _v in criados if c.email.startswith("ganho.alfa"))
        ctr = Contrato.objects.filter(cliente=ganho_alfa, organization=org).first()
        cob = Cobranca.objects.create(
            usuario=user,
            organization=org,
            cliente=ganho_alfa,
            descricao="Honorários DEMO (parcial)",
            valor_original=Decimal("8000.00"),
            data_vencimento=hoje,
            categoria=CategoriaCobranca.HONORARIOS,
            status=StatusCobranca.PAID,
            criado_por=user,
            responsavel=user,
            contrato=ctr,
        )
        CobrancaRecebimento.objects.create(
            cobranca=cob,
            usuario=user,
            organization=org,
            valor=Decimal("8000.00"),
            data_recebimento=hoje,
            registrado_por=user,
        )

    return ResumoSeedComercial(
        organization_id=org.pk,
        clientes=Cliente.objects.filter(organization=org, utm_content=UTM_CONTENT).count(),
        compromissos=Compromisso.objects.filter(
            cliente__organization=org, cliente__utm_content=UTM_CONTENT
        ).count(),
        contratos=Contrato.objects.filter(organization=org, cliente__utm_content=UTM_CONTENT).count(),
        perdas=Cliente.objects.filter(
            organization=org, utm_content=UTM_CONTENT, status="inativo"
        ).count(),
        ganhos=Contrato.objects.filter(organization=org, cliente__utm_content=UTM_CONTENT).count(),
        meta_preservada=meta_existia,
    )
