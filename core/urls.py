from django.contrib import admin
from django.urls import path, include

from . import health, views

urlpatterns = [
    path("health/", health.health, name="health"),
    path("ready/", health.ready, name="ready"),
    path(
        "media/documentos/<path:filename>",
        views.recusar_media_documentos,
        name="recusar_media_documentos",
    ),
    path("", views.home, name="home"),
    path("admin/", admin.site.urls),
    path("ia/", include("ia.urls")),
    path("usuarios/", include("usuarios.urls")),
    path("financeiro/", include("financeiro.urls")),
    path("marketing/", include("marketing.urls")),
    path("comercial/", include("comercial.urls")),
    path("martor/", include("martor.urls")),
]

