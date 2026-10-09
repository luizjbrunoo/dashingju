import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ia", "0005_vectorchunk"),
        ("organizacoes", "0001_initial"),
        ("usuarios", "0021_document_arquivo_tenant_upload_to"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SecretariaConversationState",
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
                ("channel_key", models.CharField(max_length=64)),
                ("turns", models.JSONField(blank=True, default=list)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "cliente",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="secretaria_conversations",
                        to="usuarios.cliente",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="secretaria_conversations",
                        to="organizacoes.organization",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="secretaria_conversations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="secretariaconversationstate",
            index=models.Index(
                fields=["organization", "user", "channel_key"],
                name="ia_secretaria_org_user_ch_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="secretariaconversationstate",
            constraint=models.UniqueConstraint(
                fields=["organization", "user", "channel_key"],
                name="uniq_ia_secretaria_org_user_channel",
            ),
        ),
    ]
