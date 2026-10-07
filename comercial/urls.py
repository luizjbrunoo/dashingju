from django.urls import path

from comercial import views

urlpatterns = [
    path("", views.dashboard, name="comercial_dashboard"),
    path("meta/", views.meta_editar, name="comercial_meta"),
    path("investimento/", views.investimento_listar, name="comercial_investimento"),
    path("acao/resolver/", views.acao_resolver, name="comercial_acao_resolver"),
    path("acao/criar-tarefa/", views.acao_criar_tarefa, name="comercial_acao_criar_tarefa"),
    path("acao/followups/", views.advisor_criar_followups, name="comercial_advisor_followups"),
]
