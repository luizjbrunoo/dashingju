from django.db import migrations

GRUPO_COMERCIAL = "Comercial — acesso completo"

PERMISSOES = (
    ("view_dashboard", "Pode visualizar o painel comercial"),
    ("manage_goals", "Pode criar e editar metas comerciais"),
    ("view_revenue", "Pode visualizar faturamento e gaps"),
    ("view_team", "Pode visualizar desempenho da equipe"),
    ("export_reports", "Pode exportar relatórios comerciais"),
)


def criar_grupo_comercial(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    ct = ContentType.objects.filter(app_label="comercial", model="metacomercial").first()
    if not ct:
        return

    grupo, _ = Group.objects.get_or_create(name=GRUPO_COMERCIAL)
    perm_ids = []
    for codename, _ in PERMISSOES:
        perm = Permission.objects.filter(content_type=ct, codename=codename).first()
        if perm:
            perm_ids.append(perm.pk)
    grupo.permissions.set(perm_ids)


def remover_grupo_comercial(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GRUPO_COMERCIAL).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("comercial", "0001_fase1_inicial"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(criar_grupo_comercial, remover_grupo_comercial),
    ]
