"""Simula resultados de negócio das campanhas Google Ads (demonstração)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from marketing.services.google_ads_resultados import calcular_resultados_negocio
from marketing.services.periodo import PeriodoMarketing
from marketing.services.resultados_campanhas import calcular_ranking_campanhas
from marketing.services.resultados_demo_seed import (
    limpar_demo_resultados,
    popular_resultados_google_ads_demo,
)

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Popula leads, consultas, contratos e recebimentos sintéticos "
        "atribuídos ao Google Ads para demonstrar Resultados do Negócio."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default="",
            help="Usuário dono dos dados (padrão: primeiro superuser ou primeiro usuário).",
        )
        parser.add_argument(
            "--limpar-apenas",
            action="store_true",
            help="Só remove dados demo anteriores, sem criar novos.",
        )
        parser.add_argument(
            "--sem-limpar",
            action="store_true",
            help="Não remove demo anterior antes de popular.",
        )

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        if username:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist as exc:
                raise CommandError(f"Usuário '{username}' não encontrado.") from exc
        else:
            user = (
                User.objects.filter(is_superuser=True).order_by("id").first()
                or User.objects.order_by("id").first()
            )
            if user is None:
                raise CommandError("Nenhum usuário cadastrado.")

        if options["limpar_apenas"]:
            n = limpar_demo_resultados(user)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Removidos {n} registro(s) demo de {user.username}."
                )
            )
            return

        resumo = popular_resultados_google_ads_demo(
            user, limpar=not options["sem_limpar"]
        )
        periodo = PeriodoMarketing.ultimos_dias(30)
        resultados = calcular_resultados_negocio(user, periodo, modo_demo=True)
        ranking = calcular_ranking_campanhas(
            user, periodo, modo_demo=True, nicho="busca_apreensao"
        )
        f = resultados.funil

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo Google Ads - Resultados do Negocio para '{user.username}'"
            )
        )
        self.stdout.write(
            f"  Criados: {resumo.leads} leads, {resumo.consultas} consultas, "
            f"{resumo.contratos} contratos, {resumo.recebimentos} recebimentos"
            + (f" (substituiu {resumo.removidos} anteriores)" if resumo.removidos else "")
        )
        self.stdout.write(
            f"  Funil 30d: investimento demo R$ {f.investimento} | "
            f"leads {f.leads} | consultas {f.consultas_realizadas}/"
            f"{f.consultas_agendadas} | propostas {f.propostas} | "
            f"contratos {f.contratos} | receita R$ {f.receita_contratada} | "
            f"recebido R$ {f.receita_recebida}"
        )
        if ranking.exibir:
            self.stdout.write(
                f"  Campanhas no ranking: {', '.join(c.utm_campaign for c in ranking.campanhas)}"
            )
        self.stdout.write("  Abra /marketing/ e role ate 'Resultados do negocio'.")
