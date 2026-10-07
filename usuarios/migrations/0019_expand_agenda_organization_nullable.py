import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("organizacoes", "0001_initial"),
        ("usuarios", "0018_cliente_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="compromisso",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="compromissos",
                to="organizacoes.organization",
            ),
        ),
        migrations.AddField(
            model_name="tarefa",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tarefas",
                to="organizacoes.organization",
            ),
        ),
    ]
