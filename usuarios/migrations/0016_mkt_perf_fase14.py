# Fase 14 — índices de performance para métricas de marketing

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("usuarios", "0015_cliente_atribuicao_fase1"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="cliente",
            index=models.Index(
                fields=["user", "origem", "atribuicao_confiavel", "criado_em"],
                name="usuarios_cl_mkt_leads_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="cliente",
            index=models.Index(
                fields=["user", "fase_funil"],
                name="usuarios_cl_fase_funil_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="compromisso",
            index=models.Index(
                fields=["user", "tipo", "status", "data_hora"],
                name="usuarios_co_mkt_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="compromisso",
            index=models.Index(
                fields=["user", "cliente", "data_hora", "status"],
                name="usuarios_co_cli_futuro_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="tarefa",
            index=models.Index(
                fields=["user", "cliente", "status", "prazo"],
                name="usuarios_ta_mkt_acao_idx",
            ),
        ),
    ]
