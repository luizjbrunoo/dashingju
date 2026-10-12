"""Webhook público GET/POST da Meta Cloud API. Sem autenticação de usuário."""

from __future__ import annotations

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from integrations.services.whatsapp_webhook import (
    WhatsAppWebhookError,
    ingest_whatsapp_event,
    verify_subscription,
)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def whatsapp_webhook(request):
    try:
        if request.method == "GET":
            challenge = verify_subscription(
                mode=request.GET.get("hub.mode") or "",
                token=request.GET.get("hub.verify_token") or "",
                challenge=request.GET.get("hub.challenge") or "",
            )
            return HttpResponse(challenge, content_type="text/plain; charset=utf-8")
        ingest_whatsapp_event(
            raw_body=request.body or b"",
            signature_header=request.headers.get("X-Hub-Signature-256") or "",
        )
        return HttpResponse(status=200)
    except WhatsAppWebhookError as exc:
        return HttpResponse(status=exc.http_status)
