from django.contrib import admin
from django.urls import path, include
from django.views.generic.base import RedirectView

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("favicon.ico", RedirectView.as_view(url="/static/logo_dashing_juridico.jpg", permanent=True)),
    path("admin/", admin.site.urls),
    path("ia/", include("ia.urls")),
    path("usuarios/", include("usuarios.urls")),
    path("financeiro/", include("financeiro.urls")),
    path("marketing/", include("marketing.urls")),
    path("martor/", include("martor.urls")),
]

