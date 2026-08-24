from django.db import migrations

GRUPO_MARKETING = "Marketing — acesso completo"

PERMISSOES = (
    ("view_marketing", "Pode visualizar o painel Google Ads"),
    ("view_resultados_marketing", "Pode visualizar resultados do negócio (analytics)"),
    ("view_conteudo_marketing", "Pode visualizar marketing de conteúdo"),
    ("edit_conteudo_marketing", "Pode criar e editar conteúdo de marketing"),
)


def criar_grupo_marketing(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    ct = ContentType.objects.filter(
        app_label="marketing", model="marketingintegracao"
    ).first()
    if not ct:
        return

    grupo, _ = Group.objects.get_or_create(name=GRUPO_MARKETING)
    perm_ids = []
    for codename, _ in PERMISSOES:
        perm = Permission.objects.filter(content_type=ct, codename=codename).first()
        if perm:
            perm_ids.append(perm.pk)
    grupo.permissions.set(perm_ids)


def remover_grupo_marketing(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GRUPO_MARKETING).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("marketing", "0002_marketingintegracao"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="marketingintegracao",
            options={
                "verbose_name": "Integração de marketing",
                "verbose_name_plural": "Integrações de marketing",
                "permissions": list(PERMISSOES),
            },
        ),
        migrations.RunPython(criar_grupo_marketing, remover_grupo_marketing),
    ]
