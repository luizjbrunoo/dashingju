"""Decorators de permissão para views da Agenda."""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.messages import constants
from django.shortcuts import redirect

from usuarios.permissions import (
    PERM_VIEW_AGENDA,
    PERM_VIEW_AUDIT_AGENDA,
    _tem_perm,
)


def _negado_agenda(request, mensagem: str):
    messages.add_message(request, constants.ERROR, mensagem)
    return redirect("clientes")


def requer_perm_agenda(perm: str, *, mensagem: str | None = None):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not _tem_perm(request.user, perm):
                return _negado_agenda(
                    request,
                    mensagem or "Você não tem permissão para acessar a agenda.",
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def login_e_perm_agenda(perm: str, *, mensagem: str | None = None):
    def decorator(view_func):
        @login_required
        @requer_perm_agenda(perm, mensagem=mensagem)
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


perm_ver_agenda = requer_perm_agenda(
    PERM_VIEW_AGENDA,
    mensagem="Sem permissão para visualizar a agenda.",
)
perm_ver_auditoria_agenda = requer_perm_agenda(
    PERM_VIEW_AUDIT_AGENDA,
    mensagem="Sem permissão para visualizar a auditoria da agenda.",
)
