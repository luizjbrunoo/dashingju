"""Ingress público CSRF-exempt do webhook Asaas. Autenticado por token."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from financeiro.services.asaas_client import WEBHOOK_TOKEN_HEADER
from financeiro.services.asaas_webhooks import AsaasWebhookError, ingest_asaas_webhook


@csrf_exempt
@require_POST
def asaas_webhook_ingress(request, webhook_public_id):
    token = request.headers.get(WEBHOOK_TOKEN_HEADER) or ""
    try:
        ingest_asaas_webhook(
            public_id=webhook_public_id,
            header_token=token,
            raw_body=request.body,
        )
    except AsaasWebhookError as exc:
        return JsonResponse({"ok": False}, status=exc.http_status)
    return JsonResponse({"ok": True}, status=200)
