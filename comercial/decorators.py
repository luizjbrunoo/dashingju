"""Decorators de permissão para views do Comercial."""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.messages import constants
from django.shortcuts import redirect

from comercial.permissions import (
    PERM_MANAGE_GOALS,
    PERM_VIEW_DASHBOARD,
    _tem_perm,
)


def _negado(request, mensagem: str):
    messages.add_message(request, constants.ERROR, mensagem)
    return redirect("clientes")


def requer_perm_comercial(perm: str, *, mensagem: str | None = None):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not _tem_perm(request.user, perm):
                return _negado(
                    request,
                    mensagem or "Você não tem permissão para acessar o Comercial.",
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def login_e_perm_comercial(perm: str, *, mensagem: str | None = None):
    def decorator(view_func):
        @login_required
        @requer_perm_comercial(perm, mensagem=mensagem)
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


login_e_perm_dashboard = login_e_perm_comercial(
    PERM_VIEW_DASHBOARD,
    mensagem="Sem permissão para visualizar o painel Comercial.",
)
login_e_perm_metas = login_e_perm_comercial(
    PERM_MANAGE_GOALS,
    mensagem="Sem permissão para gerenciar metas comerciais.",
)
