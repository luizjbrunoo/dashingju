from django.contrib import admin

from comercial.models import (
    AcaoComercialResolvida,
    AdvGrowthScoreSnapshot,
    ComercialAuditLog,
    InvestimentoMidia,
    MetaComercial,
)


@admin.register(MetaComercial)
class MetaComercialAdmin(admin.ModelAdmin):
    list_display = (
        "ano",
        "usuario",
        "meta_anual",
        "meta_mensal",
        "ticket_medio",
        "ticket_medio_manual",
        "atualizado_em",
    )
    list_filter = ("ano", "ticket_medio_manual")
    search_fields = ("usuario__username",)


@admin.register(ComercialAuditLog)
class ComercialAuditLogAdmin(admin.ModelAdmin):
    list_display = ("acao", "usuario", "ator", "criado_em")
    list_filter = ("acao",)
    search_fields = ("usuario__username", "detalhe")
    readonly_fields = ("usuario", "ator", "acao", "detalhe", "criado_em")


@admin.register(AcaoComercialResolvida)
class AcaoComercialResolvidaAdmin(admin.ModelAdmin):
    list_display = ("chave", "usuario", "cliente", "resolvido_por", "resolvido_em")
    search_fields = ("chave", "usuario__username")


@admin.register(InvestimentoMidia)
class InvestimentoMidiaAdmin(admin.ModelAdmin):
    list_display = ("usuario", "valor", "data_inicio", "data_fim", "canal", "criado_em")
    list_filter = ("canal",)
    search_fields = ("usuario__username", "observacoes")


@admin.register(AdvGrowthScoreSnapshot)
class AdvGrowthScoreSnapshotAdmin(admin.ModelAdmin):
    list_display = ("usuario", "data_ref", "score", "conversao", "financeiro", "criado_em")
    list_filter = ("data_ref",)
    search_fields = ("usuario__username",)
