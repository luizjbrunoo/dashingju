# Generated manually for Fase 10 — recorrência e lembretes

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("usuarios", "0009_agenda_fase1"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgendaLembrete",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("titulo", models.CharField(max_length=255)),
                ("mensagem", models.TextField(blank=True)),
                ("lido", models.BooleanField(default=False)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                (
                    "compromisso",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lembretes_disparados",
                        to="usuarios.compromisso",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agenda_lembretes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-criado_em"],
            },
        ),
        migrations.AddIndex(
            model_name="agendalembrete",
            index=models.Index(
                fields=["user", "lido", "criado_em"],
                name="usuarios_ag_user_id_6f8a2d_idx",
            ),
        ),
    ]
