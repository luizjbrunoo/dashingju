from django.db import migrations

GRUPO_AGENDA = "Agenda — acesso completo"

PERMISSOES = (
    ("view_agenda", "Pode visualizar a agenda"),
    ("create_agenda", "Pode criar compromissos e tarefas"),
    ("edit_agenda", "Pode editar compromissos e tarefas"),
    ("cancel_agenda", "Pode cancelar compromissos e tarefas"),
    ("view_audit_agenda", "Pode visualizar auditoria da agenda"),
)


def criar_grupo_agenda(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    ct = ContentType.objects.filter(app_label="usuarios", model="compromisso").first()
    if not ct:
        return

    grupo, _ = Group.objects.get_or_create(name=GRUPO_AGENDA)
    perm_ids = []
    for codename, _ in PERMISSOES:
        perm = Permission.objects.filter(content_type=ct, codename=codename).first()
        if perm:
            perm_ids.append(perm.pk)
    grupo.permissions.set(perm_ids)


def remover_grupo_agenda(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GRUPO_AGENDA).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("usuarios", "0013_agenda_fase1_campos"),
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
        migrations.RunPython(criar_grupo_agenda, remover_grupo_agenda),
    ]
