# Fase 14 — índices de performance para métricas de marketing

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0006_rbac_fase11"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="contrato",
            index=models.Index(
                fields=["usuario", "status", "criado_em"],
                name="financeiro_contr_mkt_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="cobrancarecebimento",
            index=models.Index(
                fields=["usuario", "cancelado_em", "data_recebimento"],
                name="financeiro_receb_mkt_idx",
            ),
        ),
    ]
