from django.urls import path

from . import views, views_conteudo

urlpatterns = [
    path("", views.dashboard, name="marketing_dashboard"),
    path("conteudo/", views_conteudo.dashboard, name="marketing_conteudo_dashboard"),
    path("conteudo/perfil/", views_conteudo.perfil, name="marketing_conteudo_perfil"),
    path("conteudo/biblioteca/", views_conteudo.biblioteca, name="marketing_conteudo_biblioteca"),
    path("conteudo/criar/", views_conteudo.criar, name="marketing_conteudo_criar"),
    path("conteudo/<int:pk>/editar/", views_conteudo.editar, name="marketing_conteudo_editar"),
    path("conteudo/<int:pk>/reaproveitar/", views_conteudo.reaproveitar, name="marketing_conteudo_reaproveitar"),
    path("conteudo/analytics/", views_conteudo.analytics, name="marketing_conteudo_analytics"),
    path("conteudo/integracoes/", views_conteudo.integracoes, name="marketing_conteudo_integracoes"),
    path("conteudo/calendario/plano/", views_conteudo.calendario_plano, name="marketing_conteudo_calendario_plano"),
    path("conteudo/ideias/", views_conteudo.ideias, name="marketing_conteudo_ideias"),
    path("conteudo/ideias/<int:pk>/criar/", views_conteudo.ideia_criar_conteudo, name="marketing_conteudo_ideia_criar"),
    path("conteudo/ideias/<int:pk>/agendar/", views_conteudo.ideia_agendar, name="marketing_conteudo_ideia_agendar"),
    path("conteudo/ideias/<int:pk>/descartar/", views_conteudo.ideia_descartar, name="marketing_conteudo_ideia_descartar"),
]
