"""Histórico curto da Secretaria em PostgreSQL. Organization obrigatória."""

from __future__ import annotations

import hashlib
import hmac

from django.conf import settings
from django.db import transaction

HISTORY_LIMIT = 5


def channel_key_chat(cliente_id: int) -> str:
    return f"chat:{int(cliente_id)}"


def channel_key_whatsapp(phone: str) -> str:
    digest = hmac.new(
        str(settings.SECRET_KEY).encode("utf-8"),
        str(phone or "").encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"wa:{digest}"


def format_conversation_history(turns: list | None) -> str:
    if not turns:
        return ""
    lines = []
    for turn in turns[-HISTORY_LIMIT:]:
        role = (turn or {}).get("role") or ""
        text = str((turn or {}).get("text") or "").strip()
        if not text:
            continue
        label = "Cliente" if role == "user" else "Assistente"
        lines.append(f"{label}: {text}")
    if not lines:
        return ""
    return (
        "HISTÓRICO RECENTE DESTA CONVERSA (tenant-scoped; use só isto para "
        "continuar agendamento, inclusive 'SIM para confirmar'):\n"
        + "\n".join(lines)
    )


def load_secretaria_history(*, organization, user_id, channel_key: str) -> str:
    from ia.models import SecretariaConversationState

    if organization is None or user_id is None or not channel_key:
        return ""
    state = (
        SecretariaConversationState.objects.filter(
            organization_id=organization.pk,
            user_id=user_id,
            channel_key=channel_key,
        )
        .only("turns")
        .first()
    )
    if state is None:
        return ""
    return format_conversation_history(state.turns)


def append_secretaria_turns(
    *,
    organization,
    user_id,
    channel_key: str,
    user_text: str,
    assistant_text: str,
    cliente=None,
) -> None:
    from ia.models import SecretariaConversationState

    if organization is None or user_id is None or not channel_key:
        return
    if cliente is not None:
        cli_org = getattr(cliente, "organization_id", None)
        if cli_org != organization.pk:
            return
    new_turns = []
    user_text = str(user_text or "").strip()
    assistant_text = str(assistant_text or "").strip()
    if user_text:
        new_turns.append({"role": "user", "text": user_text})
    if assistant_text:
        new_turns.append({"role": "assistant", "text": assistant_text})
    if not new_turns:
        return
    with transaction.atomic():
        state, _created = SecretariaConversationState.objects.select_for_update().get_or_create(
            organization_id=organization.pk,
            user_id=user_id,
            channel_key=channel_key,
            defaults={"cliente": cliente, "turns": []},
        )
        if (
            state.cliente_id
            and cliente is not None
            and state.cliente_id != getattr(cliente, "pk", None)
        ):
            return
        turns = list(state.turns or [])
        turns.extend(new_turns)
        state.turns = turns[-HISTORY_LIMIT:]
        if cliente is not None and state.cliente_id is None:
            state.cliente = cliente
        state.save(update_fields=["turns", "cliente", "updated_at"])
