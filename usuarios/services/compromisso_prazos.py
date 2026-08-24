"""Status e rótulos de prazos processuais em compromissos."""

from __future__ import annotations

from datetime import date

from django.utils import timezone

from usuarios.choices import StatusCompromisso, TipoCompromisso

_STATUS_ABERTOS = frozenset(
    {StatusCompromisso.AGENDADO, StatusCompromisso.CONFIRMADO}
)


def _ref(ref: date | None = None) -> date:
    return ref or timezone.localdate()


def compromisso_prazo_aberto(compromisso) -> bool:
    return (
        compromisso.tipo == TipoCompromisso.PRAZO
        and compromisso.status in _STATUS_ABERTOS
    )


def prazo_interno_vencido(compromisso, ref: date | None = None) -> bool:
    ref = _ref(ref)
    if not compromisso_prazo_aberto(compromisso) or not compromisso.prazo_interno:
        return False
    return compromisso.prazo_interno < ref


def prazo_oficial_vencido(compromisso, ref: date | None = None) -> bool:
    ref = _ref(ref)
    if not compromisso_prazo_aberto(compromisso) or not compromisso.prazo_oficial:
        return False
    return compromisso.prazo_oficial < ref


def dias_ate_prazo_interno(compromisso, ref: date | None = None) -> int | None:
    if not compromisso.prazo_interno:
        return None
    return (compromisso.prazo_interno - _ref(ref)).days


def dias_ate_prazo_oficial(compromisso, ref: date | None = None) -> int | None:
    if not compromisso.prazo_oficial:
        return None
    return (compromisso.prazo_oficial - _ref(ref)).days


def rotulo_prazo_compromisso(compromisso, ref: date | None = None) -> str:
    if compromisso.tipo != TipoCompromisso.PRAZO:
        return ""
    ref = _ref(ref)
    if prazo_interno_vencido(compromisso, ref):
        dias = (ref - compromisso.prazo_interno).days
        return f"Interno vencido há {dias} dia{'s' if dias != 1 else ''}"
    if prazo_oficial_vencido(compromisso, ref):
        dias = (ref - compromisso.prazo_oficial).days
        return f"Oficial vencido há {dias} dia{'s' if dias != 1 else ''}"
    dias_int = dias_ate_prazo_interno(compromisso, ref)
    if dias_int is not None and dias_int <= 2:
        if dias_int == 0:
            return "Prazo interno hoje"
        return f"Prazo interno em {dias_int} dia{'s' if dias_int != 1 else ''}"
    dias_of = dias_ate_prazo_oficial(compromisso, ref)
    if dias_of is not None and dias_of <= 3:
        if dias_of == 0:
            return "Prazo oficial hoje"
        return f"Prazo oficial em {dias_of} dia{'s' if dias_of != 1 else ''}"
    return ""
