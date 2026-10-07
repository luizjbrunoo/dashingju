"""Querysets Organization-owned. organization=NULL nunca é global."""

from __future__ import annotations

from usuarios.models import Cliente, Compromisso, Tarefa


def clientes_da_organizacao(organization):
    if organization is None:
        return Cliente.objects.none()
    return Cliente.objects.filter(organization=organization)


def compromissos_da_organizacao(organization):
    if organization is None:
        return Compromisso.objects.none()
    return Compromisso.objects.filter(organization=organization)


def tarefas_da_organizacao(organization):
    if organization is None:
        return Tarefa.objects.none()
    return Tarefa.objects.filter(organization=organization)
