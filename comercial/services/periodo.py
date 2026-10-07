"""Período de análise do Comercial — reutiliza o mesmo contrato de datas do Marketing."""

from marketing.services.periodo import (  # noqa: F401
    PeriodoMarketing as PeriodoComercial,
    datetime_fim_exclusivo,
    datetime_inicio,
    filtro_datetime_campo,
)
