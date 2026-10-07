from django.db import migrations

GRUPO_AGENDA = "Agenda — acesso completo"

PERMISSOES = (
    ("view_agenda", "Pode visualizar a agenda"),
    ("create_agenda", "Pode criar compromissos e tarefas"),
    ("edit_agenda", "Pode editar compromissos e tarefas"),
    ("cancel_agenda", "Pode cancelar compromissos e tarefas"),
    ("view_audit_agenda", "Pode visualizar auditoria da agenda"),
)


def restaurar_permissoes_agenda(apps, schema_editor):
    """0017 removeu Meta.permissions do estado; bancos frescos não criavam as rows."""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    ct, _ = ContentType.objects.get_or_create(
        app_label="usuarios", model="compromisso"
    )
    perm_ids = []
    for codename, name in PERMISSOES:
        perm, _ = Permission.objects.get_or_create(
            content_type=ct,
            codename=codename,
            defaults={"name": name},
        )
        perm_ids.append(perm.pk)
    grupo, _ = Group.objects.get_or_create(name=GRUPO_AGENDA)
    grupo.permissions.set(perm_ids)


def noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("usuarios", "0019_expand_agenda_organization_nullable"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="compromisso",
            options={
                "ordering": ["data_hora"],
                "permissions": list(PERMISSOES),
            },
        ),
        migrations.RunPython(restaurar_permissoes_agenda, noop),
    ]
