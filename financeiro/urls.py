from django.urls import path

from . import views
from . import views_cobrancas
from . import views_contratos

urlpatterns = [
    path("", views.dashboard, name="financeiro_dashboard"),
    path("cobrancas/", views_cobrancas.cobranca_listar, name="financeiro_cobranca_listar"),
    path(
        "cobrancas/inadimplencia/",
        views_cobrancas.cobranca_inadimplencia,
        name="financeiro_cobranca_inadimplencia",
    ),
    path(
        "cobrancas/previsao/",
        views_cobrancas.cobranca_previsao,
        name="financeiro_cobranca_previsao",
    ),
    path(
        "cobrancas/auditoria/",
        views_cobrancas.cobranca_auditoria,
        name="financeiro_cobranca_auditoria",
    ),
    path("cobrancas/nova/", views_cobrancas.cobranca_nova, name="financeiro_cobranca_nova"),
    path("cobrancas/<int:pk>/", views_cobrancas.cobranca_detalhe, name="financeiro_cobranca_detalhe"),
    path(
        "cobrancas/<int:pk>/editar/",
        views_cobrancas.cobranca_editar,
        name="financeiro_cobranca_editar",
    ),
    path(
        "cobrancas/<int:pk>/cancelar/",
        views_cobrancas.cobranca_cancelar,
        name="financeiro_cobranca_cancelar",
    ),
    path(
        "cobrancas/<int:pk>/recebimento/",
        views_cobrancas.cobranca_registrar_recebimento,
        name="financeiro_cobranca_recebimento",
    ),
    path(
        "cobrancas/<int:pk>/recebimento/<int:recebimento_id>/estornar/",
        views_cobrancas.cobranca_estornar_recebimento,
        name="financeiro_cobranca_estornar_recebimento",
    ),
    path(
        "cobrancas/<int:pk>/agendar/",
        views_cobrancas.cobranca_agendar_agenda,
        name="financeiro_cobranca_agendar",
    ),
    path(
        "cobrancas/<int:pk>/cobrar/",
        views_cobrancas.cobranca_cobrar,
        name="financeiro_cobranca_cobrar",
    ),
    path("contratos/", views_contratos.contrato_listar, name="financeiro_contrato_listar"),
    path("contratos/novo/", views_contratos.contrato_novo, name="financeiro_contrato_novo"),
    path("contratos/<int:pk>/", views_contratos.contrato_detalhe, name="financeiro_contrato_detalhe"),
    path(
        "contratos/<int:pk>/editar/",
        views_contratos.contrato_editar,
        name="financeiro_contrato_editar",
    ),
    path(
        "contratos/<int:pk>/gerar-cobrancas/",
        views_contratos.contrato_gerar_cobrancas,
        name="financeiro_contrato_gerar_cobrancas",
    ),
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
