from django.db import migrations


class Migration(migrations.Migration):
    """FIN-AUTH: capabilities explícitas de Caixa. Sem drift de CobrancaHistorico.acao."""

    dependencies = [
        ("financeiro", "0008_expand_organization_nullable"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="banco",
            options={
                "ordering": ["nome"],
                "permissions": [
                    ("view_caixa", "Pode visualizar caixa"),
                    ("manage_caixa", "Pode movimentar caixa"),
                ],
                "verbose_name": "Banco",
                "verbose_name_plural": "Bancos",
            },
        ),
    ]
