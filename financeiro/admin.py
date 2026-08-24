from django.contrib import admin

from .models import (
    Banco,
    Categoria,
    Cobranca,
    CobrancaHistorico,
    CobrancaRecebimento,
    Contrato,
    Movimento,
)


@admin.register(Banco)
class BancoAdmin(admin.ModelAdmin):
    list_display = ("nome", "usuario", "agencia", "conta", "saldo_inicial")
    list_filter = ("usuario",)
    search_fields = ("nome", "conta")


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nome", "tipo", "usuario")
    list_filter = ("tipo", "usuario")
    search_fields = ("nome",)


@admin.register(Movimento)
class MovimentoAdmin(admin.ModelAdmin):
    list_display = ("data", "banco", "categoria", "valor", "usuario")
    list_filter = ("data", "categoria__tipo", "banco")
    search_fields = ("descricao",)
    date_hierarchy = "data"


@admin.register(Contrato)
class ContratoAdmin(admin.ModelAdmin):
    list_display = ("referencia", "cliente", "valor_total", "status", "usuario")
    list_filter = ("status", "usuario")
    search_fields = ("referencia", "descricao", "cliente__nome")


@admin.register(Cobranca)
class CobrancaAdmin(admin.ModelAdmin):
    list_display = (
        "descricao",
        "cliente",
        "valor_original",
        "data_vencimento",
        "status",
        "usuario",
    )
    list_filter = ("status", "categoria", "usuario")
    search_fields = ("descricao", "cliente__nome", "contrato_referencia")
    date_hierarchy = "data_vencimento"


@admin.register(CobrancaRecebimento)
class CobrancaRecebimentoAdmin(admin.ModelAdmin):
    list_display = ("cobranca", "valor", "data_recebimento", "usuario", "cancelado_em")
    list_filter = ("forma_pagamento", "usuario")
    date_hierarchy = "data_recebimento"


@admin.register(CobrancaHistorico)
class CobrancaHistoricoAdmin(admin.ModelAdmin):
    list_display = ("cobranca", "acao", "autor", "criado_em")
    list_filter = ("acao", "usuario")
    date_hierarchy = "criado_em"
