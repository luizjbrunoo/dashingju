from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="financeiro_dashboard"),
    path("bancos/", views.banco_listar, name="financeiro_banco_listar"),
    path("bancos/novo/", views.banco_novo, name="financeiro_banco_novo"),
    path("bancos/<int:pk>/editar/", views.banco_editar, name="financeiro_banco_editar"),
    path("bancos/<int:pk>/excluir/", views.banco_excluir, name="financeiro_banco_excluir"),
    path("categorias/", views.categoria_listar, name="financeiro_categoria_listar"),
    path("categorias/nova/", views.categoria_nova, name="financeiro_categoria_nova"),
    path("categorias/<int:pk>/editar/", views.categoria_editar, name="financeiro_categoria_editar"),
    path("categorias/<int:pk>/excluir/", views.categoria_excluir, name="financeiro_categoria_excluir"),
    path("extrato/", views.extrato, name="financeiro_extrato"),
    path("lancamentos/novo/", views.movimento_novo, name="financeiro_movimento_novo"),
    path("lancamentos/<int:pk>/editar/", views.movimento_editar, name="financeiro_movimento_editar"),
    path("lancamentos/<int:pk>/excluir/", views.movimento_excluir, name="financeiro_movimento_excluir"),
    path("relatorio.pdf", views.relatorio_pdf, name="financeiro_relatorio_pdf"),
]
