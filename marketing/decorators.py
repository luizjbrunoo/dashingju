"""Decorators de permissão para views de Marketing."""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.messages import constants
from django.shortcuts import redirect

from marketing.permissions import (
    PERM_EDIT_CONTEUDO,
    PERM_MANAGE_INTEGRACOES,
    PERM_VIEW_CONTEUDO,
    PERM_VIEW_MARKETING,
    _tem_perm,
)


def _negado(request, mensagem: str):
    messages.add_message(request, constants.ERROR, mensagem)
    return redirect("clientes")


def requer_perm_marketing(perm: str, *, mensagem: str | None = None):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not _tem_perm(request.user, perm):
                return _negado(
                    request,
                    mensagem or "Você não tem permissão para acessar o marketing.",
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def login_e_perm_marketing(perm: str, *, mensagem: str | None = None):
    def decorator(view_func):
        @login_required
        @requer_perm_marketing(perm, mensagem=mensagem)
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


login_e_perm_google_ads = login_e_perm_marketing(
    PERM_VIEW_MARKETING,
    mensagem="Sem permissão para visualizar o painel Google Ads.",
)
login_e_perm_conteudo = login_e_perm_marketing(
    PERM_VIEW_CONTEUDO,
    mensagem="Sem permissão para visualizar o marketing de conteúdo.",
)
login_e_perm_edit_conteudo = login_e_perm_marketing(
    PERM_EDIT_CONTEUDO,
    mensagem="Sem permissão para criar ou editar conteúdo de marketing.",
)
login_e_perm_manage_integracoes = login_e_perm_marketing(
    PERM_MANAGE_INTEGRACOES,
    mensagem="Sem permissão para gerenciar integrações de marketing.",
)
