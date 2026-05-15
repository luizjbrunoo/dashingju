from django.urls import path
from . import views

urlpatterns = [
    path("cadastro/", views.cadastro, name="cadastro"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("agenda/", views.agenda, name="agenda"),
    path("clientes/", views.clientes, name='clientes'),
    path("cliente", views.cliente_home, name="cliente_home_no_slash"),
    path("cliente/", views.cliente_home, name="cliente_home"),
    path("cliente/<int:id>", views.cliente, name='cliente'),
]