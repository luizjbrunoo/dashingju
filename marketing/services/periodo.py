"""Período de análise para métricas de Marketing / Google Ads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.utils import timezone


def datetime_inicio(d: date) -> datetime:
    """Início do dia (timezone-aware) para filtros __gte em DateTimeField."""
    return timezone.make_aware(datetime.combine(d, time.min))


def datetime_fim_exclusivo(d: date) -> datetime:
    """Início do dia seguinte — limite exclusivo para intervalos inclusivos em date."""
    return timezone.make_aware(datetime.combine(d + timedelta(days=1), time.min))


def filtro_datetime_campo(
    qs,
    campo: str,
    *,
    data_inicio: date | None = None,
    data_fim: date | None = None,
):
    """Aplica intervalo [data_inicio, data_fim] inclusivo sem lookup __date."""
    if data_inicio is not None:
        qs = qs.filter(**{f"{campo}__gte": datetime_inicio(data_inicio)})
    if data_fim is not None:
        qs = qs.filter(**{f"{campo}__lt": datetime_fim_exclusivo(data_fim)})
    return qs

@dataclass(frozen=True)
class PeriodoMarketing:
    data_inicio: date
    data_fim: date

    def __post_init__(self) -> None:
        if self.data_fim < self.data_inicio:
            raise ValueError("data_fim não pode ser anterior a data_inicio.")

    @property
    def dias(self) -> int:
        return (self.data_fim - self.data_inicio).days + 1

    @classmethod
    def ultimos_dias(cls, dias: int = 30, *, referencia: date | None = None) -> PeriodoMarketing:
        fim = referencia or timezone.localdate()
        inicio = fim - timedelta(days=max(dias - 1, 0))
        return cls(data_inicio=inicio, data_fim=fim)

    @classmethod
    def from_parametros(
        cls,
        data_inicio: date | None,
        data_fim: date | None,
        *,
        padrao_dias: int = 30,
    ) -> PeriodoMarketing:
        if data_inicio and data_fim:
            return cls(data_inicio=data_inicio, data_fim=data_fim)
        if data_inicio and not data_fim:
            return cls(data_inicio=data_inicio, data_fim=timezone.localdate())
        if data_fim and not data_inicio:
            return cls(
                data_inicio=data_fim - timedelta(days=padrao_dias - 1),
                data_fim=data_fim,
            )
        return cls.ultimos_dias(padrao_dias)

    def periodo_anterior(self) -> PeriodoMarketing:
        """Período imediatamente anterior com a mesma duração (comparação temporal)."""
        delta = timedelta(days=self.dias)
        fim_anterior = self.data_inicio - timedelta(days=1)
        inicio_anterior = fim_anterior - delta + timedelta(days=1)
        return PeriodoMarketing(data_inicio=inicio_anterior, data_fim=fim_anterior)

    def contem(self, d: date) -> bool:
        return self.data_inicio <= d <= self.data_fim

    def label(self) -> str:
        return f"{self.data_inicio.strftime('%d/%m/%Y')} a {self.data_fim.strftime('%d/%m/%Y')}"
