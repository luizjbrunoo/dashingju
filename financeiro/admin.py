from django.contrib import admin

from .models import Banco, Categoria, Movimento


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
