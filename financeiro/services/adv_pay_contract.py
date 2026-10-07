"""Contrato arquitetural ADV Pay / Asaas / régua — sem implementação de provider.

Nesta fase: nenhuma API Asaas, nenhum disparo, nenhuma tarifa, nenhum model novo.
Financeiro permanece a fonte de verdade operacional (Contrato ≠ Cobrança ≠ Recebimento).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Provider boundary (futuro)
# Financeiro → PaymentProvider (interface) → AsaasAdapter
# IDs externos nunca devem vazar para templates/services de produto como asaas_id.
# ---------------------------------------------------------------------------
PROVIDER_BOUNDARY = "financeiro -> payment_provider -> asaas_adapter"
EXTERNAL_FIELDS = ("provider", "external_id", "external_status", "external_reference")

# Ownership: tudo financeiro futuro pertence à Organization, nunca ao User.
OWNER_ORGANIZATION = "organization"

# Eventos internos (não são nomes Asaas). Adapter futuro mapeia webhooks → estes.
PAYMENT_EVENTS = (
    "PAYMENT_CREATED",
    "PAYMENT_CONFIRMED",
    "PAYMENT_OVERDUE",
    "PAYMENT_REFUNDED",
    "PAYMENT_CANCELLED",
)

# Webhook repetido ≠ recebimento duplicado. Idempotência por (organization, provider, external_id).
IDEMPOTENCY_INVARIANT = "duplicate_webhook_does_not_duplicate_receipt"

# Economia: nunca misturar. Nenhuma tarifa inventada nesta fase.
TPV_IS_NOT_ADV_REVENUE = True
OFFICE_FEES_ARE_NOT_ADV_REVENUE = True
PROVIDER_COST_NE_ADV_REVENUE_NE_MARGIN_NE_OFFICE_CHARGE = True

# Régua futura: Organization-owned; canais WhatsApp/E-mail da Organization.
# Não reutilizar IA_WHATSAPP_USER_ID.
REGUA_OWNER = "organization"
REGUA_WINDOWS = ("before_due", "on_due", "after_due")
REGUA_CHANNELS = ("whatsapp", "email")
PAYMENT_STOPS_REGUA = True
# Nunca duplicar comunicação Asaas nativa + régua ADV para o mesmo evento.
DEDUP_PROVIDER_VS_ADV = True

# Monetização conceptualmente suportável; não escolhida e não cobrada agora.
MONETIZATION_PER_DISPATCH = "READY"
MONETIZATION_FRANCHISE_OVERAGE = "READY"
MONETIZATION_AGGREGATE_TARIFF = "READY"

# Auditoria futura da régua (não persistida agora).
REGUA_AUDIT_STATES = (
    "scheduled",
    "attempted",
    "sent",
    "delivered",
    "failed",
    "cancelled",
    "payment_confirmed",
    "regua_interrupted",
)
