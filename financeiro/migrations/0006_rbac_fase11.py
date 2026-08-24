from django.db import migrations


GRUPO_FINANCEIRO = "Financeiro — acesso completo"

PERMISSOES = (
    ("view_cobrancas", "Pode visualizar cobranças"),
    ("create_cobrancas", "Pode criar cobranças"),
    ("edit_cobrancas", "Pode editar cobranças"),
    ("cancel_cobrancas", "Pode cancelar cobranças"),
    ("view_relatorios_cobrancas", "Pode visualizar relatórios de cobranças"),
    ("view_recebimentos", "Pode visualizar recebimentos"),
    ("create_recebimentos", "Pode registrar recebimentos"),
)


def criar_grupo_financeiro(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    ct_cobranca = ContentType.objects.filter(app_label="financeiro", model="cobranca").first()
    ct_receb = ContentType.objects.filter(
        app_label="financeiro", model="cobrancarecebimento"
    ).first()
    if not ct_cobranca or not ct_receb:
        return

    grupo, _ = Group.objects.get_or_create(name=GRUPO_FINANCEIRO)
    perm_ids = []
    for codename, _ in PERMISSOES[:5]:
        perm = Permission.objects.filter(content_type=ct_cobranca, codename=codename).first()
        if perm:
            perm_ids.append(perm.pk)
    for codename, _ in PERMISSOES[5:]:
        perm = Permission.objects.filter(content_type=ct_receb, codename=codename).first()
        if perm:
            perm_ids.append(perm.pk)
    grupo.permissions.set(perm_ids)


def remover_grupo_financeiro(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GRUPO_FINANCEIRO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0005_cobranca_agenda_fase7"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="cobranca",
            options={
                "ordering": ["data_vencimento", "id"],
                "permissions": [
                    ("view_cobrancas", "Pode visualizar cobranças"),
                    ("create_cobrancas", "Pode criar cobranças"),
                    ("edit_cobrancas", "Pode editar cobranças"),
                    ("cancel_cobrancas", "Pode cancelar cobranças"),
                    ("view_relatorios_cobrancas", "Pode visualizar relatórios de cobranças"),
                ],
                "verbose_name": "Cobrança",
                "verbose_name_plural": "Cobranças",
            },
        ),
        migrations.AlterModelOptions(
            name="cobrancarecebimento",
            options={
                "ordering": ["-data_recebimento", "-id"],
                "permissions": [
                    ("view_recebimentos", "Pode visualizar recebimentos"),
                    ("create_recebimentos", "Pode registrar recebimentos"),
                ],
                "verbose_name": "Recebimento de cobrança",
                "verbose_name_plural": "Recebimentos de cobrança",
            },
        ),
        migrations.RunPython(criar_grupo_financeiro, remover_grupo_financeiro),
    ]
