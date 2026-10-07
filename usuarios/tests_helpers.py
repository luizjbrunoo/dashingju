"""Helpers de teste para Django Permissions da Agenda."""

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

AGENDA_CODENAMES = (
    "view_agenda",
    "create_agenda",
    "edit_agenda",
    "cancel_agenda",
    "view_audit_agenda",
)

AGENDA_PERM_NAMES = {
    "view_agenda": "Pode visualizar a agenda",
    "create_agenda": "Pode criar compromissos e tarefas",
    "edit_agenda": "Pode editar compromissos e tarefas",
    "cancel_agenda": "Pode cancelar compromissos e tarefas",
    "view_audit_agenda": "Pode visualizar auditoria da agenda",
}


def grant_agenda_permissions(user, *codenames):
    codes = codenames or AGENDA_CODENAMES
    ct = ContentType.objects.get(app_label="usuarios", model="compromisso")
    perms = []
    for code in codes:
        perm, _ = Permission.objects.get_or_create(
            content_type=ct,
            codename=code,
            defaults={"name": AGENDA_PERM_NAMES.get(code, code)},
        )
        perms.append(perm)
    user.user_permissions.add(*perms)


def grant_documentos_permissions(user):
    ct = ContentType.objects.get(app_label="usuarios", model="documentos")
    perm = Permission.objects.get(content_type=ct, codename="view_documentos")
    user.user_permissions.add(perm)
