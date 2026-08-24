"""Decorators de permissão para views financeiras."""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.messages import constants
from django.shortcuts import redirect

from financeiro.permissions import (
    PERM_CANCEL_COBRANCAS,
    PERM_CREATE_COBRANCAS,
    PERM_CREATE_RECEBIMENTOS,
    PERM_EDIT_COBRANCAS,
    PERM_VIEW_COBRANCAS,
    PERM_VIEW_RECEBIMENTOS,
    PERM_VIEW_RELATORIOS,
    _tem_perm,
)


def _negado(request, mensagem: str):
    messages.add_message(request, constants.ERROR, mensagem)
    return redirect("financeiro_dashboard")


def requer_perm(perm: str, *, mensagem: str | None = None):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not _tem_perm(request.user, perm):
                return _negado(
                    request,
                    mensagem or "Você não tem permissão para acessar esta área financeira.",
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def login_e_perm(perm: str, *, mensagem: str | None = None):
    """Combina login_required + verificação de permissão."""

    def decorator(view_func):
        @login_required
        @requer_perm(perm, mensagem=mensagem)
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


# Atalhos usados nas views
perm_ver_cobrancas = requer_perm(
    PERM_VIEW_COBRANCAS,
    mensagem="Sem permissão para visualizar cobranças.",
)
perm_criar_cobrancas = requer_perm(
    PERM_CREATE_COBRANCAS,
    mensagem="Sem permissão para criar cobranças.",
)
perm_editar_cobrancas = requer_perm(
    PERM_EDIT_COBRANCAS,
    mensagem="Sem permissão para editar cobranças.",
)
perm_cancelar_cobrancas = requer_perm(
    PERM_CANCEL_COBRANCAS,
    mensagem="Sem permissão para cancelar cobranças.",
)
perm_ver_recebimentos = requer_perm(
    PERM_VIEW_RECEBIMENTOS,
    mensagem="Sem permissão para visualizar recebimentos.",
)
perm_registrar_recebimentos = requer_perm(
    PERM_CREATE_RECEBIMENTOS,
    mensagem="Sem permissão para registrar recebimentos.",
)
perm_ver_relatorios = requer_perm(
    PERM_VIEW_RELATORIOS,
    mensagem="Sem permissão para visualizar relatórios financeiros.",
)
