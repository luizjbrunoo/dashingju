from django.urls import path

from . import views_whatsapp

urlpatterns = [
    path(
        "whatsapp/webhook/",
        views_whatsapp.whatsapp_webhook,
        name="integrations_whatsapp_webhook",
    ),
]
