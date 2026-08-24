# Generated manually — Fase 1 Agenda: campos consulta + índices

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("usuarios", "0012_tarefa_metadados_fase7"),
    ]

    operations = [
        migrations.AddField(
            model_name="compromisso",
            name="confirmacao_consulta",
            field=models.CharField(
                blank=True,
                choices=[
                    ("pendente", "Pendente"),
                    ("confirmada", "Confirmada"),
                    ("cancelada", "Cancelada"),
                ],
                default="",
                help_text="Status de confirmação para consultas.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="compromisso",
            name="origem_lead",
            field=models.CharField(
                blank=True,
                choices=[
                    ("indicacao", "Indicação"),
                    ("site", "Site"),
                    ("redes_sociais", "Redes sociais"),
                    ("telefone", "Telefone"),
                    ("outro", "Outro"),
                ],
                default="",
                help_text="Origem do lead para consultas.",
                max_length=30,
            ),
        ),
        migrations.AddIndex(
            model_name="compromisso",
            index=models.Index(
                fields=["user", "prazo_interno"],
                name="usuarios_co_user_id_6a8f2d_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="compromisso",
            index=models.Index(
                fields=["user", "confirmacao_consulta"],
                name="usuarios_co_user_id_9c4e1a_idx",
            ),
        ),
    ]
