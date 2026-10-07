import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0007_mkt_perf_fase14"),
        ("organizacoes", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="banco",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="bancos_financeiro",
                to="organizacoes.organization",
            ),
        ),
        migrations.AddField(
            model_name="categoria",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="categorias_financeiro",
                to="organizacoes.organization",
            ),
        ),
        migrations.AddField(
            model_name="cobranca",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cobrancas_financeiro",
                to="organizacoes.organization",
            ),
        ),
        migrations.AddField(
            model_name="cobrancarecebimento",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="recebimentos_financeiro",
                to="organizacoes.organization",
            ),
        ),
        migrations.AddField(
            model_name="contrato",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="contratos_financeiro",
                to="organizacoes.organization",
            ),
        ),
        migrations.AddField(
            model_name="movimento",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="movimentos_financeiro",
                to="organizacoes.organization",
            ),
        ),
    ]
