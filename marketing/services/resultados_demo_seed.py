"""
Popula leads/consultas/contratos sintéticos atribuídos ao Google Ads
para demonstrar o painel Resultados do Negócio (sem API).

Marcação: e-mail `@dashingju.demo` e `utm_content=dashingju_demo_seed`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from financeiro.choices import (
    CategoriaCobranca,
    FormaPagamento,
    StatusCobranca,
    StatusContrato,
)
from financeiro.models import Cobranca, CobrancaRecebimento, Contrato
from usuarios.choices import (
    OrigemLead,
    OrigemLeadConsulta,
    StatusCompromisso,
    TipoCompromisso,
)
from usuarios.models import Cliente, Compromisso

UTM_CONTENT_DEMO = "dashingju_demo_seed"
EMAIL_SUFFIX = "@dashingju.demo"

# Campanhas alinhadas aos nichos do planejamento demo.
CAMPANHAS = (
    ("busca_apreensao", "Busca e Apreensão"),
    ("acao_trabalhista", "Ação trabalhista"),
    ("registro_marcas", "Registro de Marcas"),
    ("desbloqueio_conta_bancaria", "Desbloqueio de conta bancária"),
)

NOMES = (
    "Ana Souza",
    "Bruno Lima",
    "Carla Mendes",
    "Diego Alves",
    "Elena Rocha",
    "Fábio Nunes",
    "Gabriela Dias",
    "Henrique Costa",
    "Isabela Freitas",
    "João Pedro Santos",
    "Karina Oliveira",
    "Lucas Martins",
    "Marina Ferreira",
    "Nicolas Barbosa",
    "Olivia Campos",
    "Paulo Henrique",
    "Queila Ramos",
    "Rafael Teixeira",
    "Sofia Andrade",
    "Thiago Moreira",
    "Úrsula Pinto",
    "Vitor Hugo",
    "Wanessa Cruz",
    "Xavier Gomes",
    "Yasmin Lopes",
    "Zeca Araújo",
    "Amanda Vieira",
    "Breno Carvalho",
)


@dataclass(frozen=True)
class ResumoSeedResultados:
    leads: int
    consultas: int
    contratos: int
    recebimentos: int
    removidos: int


def _aware(dia, hora=10, minuto=0):
    return timezone.make_aware(datetime.combine(dia, time(hour=hora, minute=minuto)))


def _qs_demo(user):
    return Cliente.objects.filter(user=user, utm_content=UTM_CONTENT_DEMO)


def limpar_demo_resultados(user) -> int:
    """Remove dados de demonstração anteriores do tenant."""
    leads = list(_qs_demo(user).values_list("pk", flat=True))
    if not leads:
        return 0

    CobrancaRecebimento.objects.filter(
        usuario=user, cobranca__cliente_id__in=leads
    ).delete()
    Cobranca.objects.filter(usuario=user, cliente_id__in=leads).delete()
    Contrato.objects.filter(usuario=user, cliente_id__in=leads).delete()
    Compromisso.objects.filter(user=user, cliente_id__in=leads).delete()
    n, _ = Cliente.objects.filter(pk__in=leads).delete()
    return n


def popular_resultados_google_ads_demo(
    user,
    *,
    limpar: bool = True,
) -> ResumoSeedResultados:
    """
    Cria funil coerente no período recente (~45 dias):
    leads Ads → consultas → propostas → contratos → receita recebida,
    com ranking por utm_campaign e alguns alertas operacionais.
    """
    removidos = limpar_demo_resultados(user) if limpar else 0
    hoje = timezone.localdate()

    planos: list[dict] = []
    # Distribuição por campanha (leads totais = 28)
    # Estágios: lead_only | consulta_agendada | consulta_realizada |
    #           proposta | contrato | contrato_pago | sem_acao | followup_atrasado
    etapas_ciclo = (
        ["lead_only"] * 4
        + ["consulta_agendada"] * 3
        + ["consulta_realizada"] * 5
        + ["proposta"] * 4
        + ["contrato"] * 3
        + ["contrato_pago"] * 4
        + ["sem_acao"] * 3
        + ["followup_atrasado"] * 2
    )
    assert len(etapas_ciclo) == 28

    for i, etapa in enumerate(etapas_ciclo):
        camp_key, _ = CAMPANHAS[i % len(CAMPANHAS)]
        dias_atras = 2 + (i * 1) % 40
        planos.append(
            {
                "i": i,
                "etapa": etapa,
                "campanha": camp_key,
                "dias_atras": dias_atras,
                "nome": NOMES[i % len(NOMES)],
            }
        )

    n_consultas = 0
    n_contratos = 0
    n_receb = 0

    with transaction.atomic():
        for p in planos:
            i = p["i"]
            criado = hoje - timedelta(days=p["dias_atras"])
            email = f"demo.gads.{user.pk}.{i:02d}{EMAIL_SUFFIX}"
            lead = Cliente.objects.create(
                user=user,
                nome=f"[Demo Ads] {p['nome']}",
                email=email,
                telefone=f"1199{100000 + i:06d}"[:11],
                tipo="PF",
                status="em_prospeccao",
                fase_funil="primeiro_contato",
                origem=OrigemLead.GOOGLE_ADS,
                atribuicao_confiavel=True,
                utm_source="google",
                utm_medium="cpc",
                utm_campaign=p["campanha"],
                utm_content=UTM_CONTENT_DEMO,
                utm_term=p["campanha"].replace("_", " "),
                gclid=f"demo_gclid_{user.pk}_{i:03d}",
                campaign_id=f"demo_{p['campanha']}",
            )
            Cliente.objects.filter(pk=lead.pk).update(criado_em=_aware(criado, 9, 15))

            etapa = p["etapa"]
            dia_consulta = criado + timedelta(days=min(5, max(1, p["dias_atras"] // 4)))
            if dia_consulta > hoje:
                dia_consulta = hoje

            if etapa in (
                "consulta_agendada",
                "consulta_realizada",
                "proposta",
                "contrato",
                "contrato_pago",
                "followup_atrasado",
            ):
                status_c = (
                    StatusCompromisso.CONFIRMADO
                    if etapa == "consulta_agendada"
                    else StatusCompromisso.REALIZADO
                )
                if etapa == "consulta_agendada":
                    # consulta futura próxima
                    data_h = _aware(hoje + timedelta(days=2 + (i % 5)), 14, 30)
                    status_c = StatusCompromisso.CONFIRMADO
                else:
                    data_h = _aware(dia_consulta, 10 + (i % 6), 0)
                Compromisso.objects.create(
                    user=user,
                    cliente=lead,
                    titulo=f"Consulta Google Ads — {p['nome']}",
                    tipo=TipoCompromisso.CONSULTA,
                    status=status_c,
                    data_hora=data_h,
                    origem_lead=OrigemLeadConsulta.GOOGLE_ADS,
                    responsavel=user,
                )
                n_consultas += 1

            if etapa in ("proposta", "contrato", "contrato_pago", "followup_atrasado"):
                fase = "proposta_enviada" if i % 2 == 0 else "aguardando_decisao"
                Cliente.objects.filter(pk=lead.pk).update(fase_funil=fase)
                lead.fase_funil = fase

            if etapa == "followup_atrasado":
                Compromisso.objects.create(
                    user=user,
                    cliente=lead,
                    titulo=f"Follow-up comercial — {p['nome']}",
                    tipo=TipoCompromisso.FOLLOWUP_COMERCIAL,
                    status=StatusCompromisso.AGENDADO,
                    data_hora=_aware(hoje - timedelta(days=3 + (i % 4)), 11, 0),
                    responsavel=user,
                )

            if etapa in ("contrato", "contrato_pago"):
                valor = Decimal(["4500.00", "7800.00", "12000.00", "3500.00", "9600.00"][i % 5])
                dia_contrato = dia_consulta + timedelta(days=3)
                if dia_contrato > hoje:
                    dia_contrato = hoje
                ref = f"DEMO-ADS-{user.pk}-{i:03d}"
                contrato = Contrato.objects.create(
                    usuario=user,
                    cliente=lead,
                    referencia=ref,
                    descricao=f"Honorários — campanha {p['campanha']}",
                    valor_total=valor,
                    status=StatusContrato.ACTIVE,
                    responsavel=user,
                    criado_por=user,
                    observacoes="Contrato sintético para demonstração de Resultados do Negócio.",
                )
                Contrato.objects.filter(pk=contrato.pk).update(
                    criado_em=_aware(dia_contrato, 16, 0)
                )
                Cliente.objects.filter(pk=lead.pk).update(
                    status="ativo", fase_funil="aguardando_decisao"
                )
                n_contratos += 1

                if etapa == "contrato_pago":
                    cob = Cobranca.objects.create(
                        usuario=user,
                        cliente=lead,
                        contrato=contrato,
                        contrato_referencia=ref,
                        descricao=f"Honorários iniciais — {p['nome']}",
                        valor_original=valor,
                        data_vencimento=dia_contrato,
                        categoria=CategoriaCobranca.HONORARIOS,
                        status=StatusCobranca.PAID,
                        responsavel=user,
                        forma_prevista_pagamento=FormaPagamento.PIX,
                        criado_por=user,
                    )
                    CobrancaRecebimento.objects.create(
                        cobranca=cob,
                        usuario=user,
                        valor=valor,
                        data_recebimento=min(hoje, dia_contrato + timedelta(days=2)),
                        forma_pagamento=FormaPagamento.PIX,
                        referencia=f"PIX-DEMO-{i:03d}",
                        observacao="Recebimento demo Google Ads",
                        registrado_por=user,
                    )
                    n_receb += 1

            # sem_acao: lead sem compromisso futuro / tarefa — já fica assim

    return ResumoSeedResultados(
        leads=len(planos),
        consultas=n_consultas,
        contratos=n_contratos,
        recebimentos=n_receb,
        removidos=removidos,
    )
