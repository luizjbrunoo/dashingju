# Generated manually — Fase 1 Marketing: atribuição de origem do lead

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("usuarios", "0014_agenda_rbac_fase12"),
    ]

    operations = [
        migrations.AddField(
            model_name="cliente",
            name="origem",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Origem não identificada"),
                    ("google_ads", "Google Ads"),
                    ("google_organic", "Google orgânico"),
                    ("instagram", "Instagram"),
                    ("facebook", "Facebook"),
                    ("whatsapp", "WhatsApp"),
                    ("indicacao", "Indicação"),
                    ("site", "Site"),
                    ("blog", "Blog"),
                    ("outro", "Outro"),
                ],
                db_index=True,
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="cliente",
            name="atribuicao_confiavel",
            field=models.BooleanField(
                default=False,
                help_text="True quando a origem foi determinada com critérios objetivos.",
            ),
        ),
        migrations.AddField(
            model_name="cliente",
            name="utm_source",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="cliente",
            name="utm_medium",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="cliente",
            name="utm_campaign",
            field=models.CharField(blank=True, db_index=True, max_length=120),
        ),
        migrations.AddField(
            model_name="cliente",
            name="utm_content",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="cliente",
            name="utm_term",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="cliente",
            name="gclid",
            field=models.CharField(blank=True, db_index=True, max_length=255),
        ),
        migrations.AddField(
            model_name="cliente",
            name="campaign_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="cliente",
            name="criado_em",
            field=models.DateTimeField(
                auto_now_add=True,
                db_index=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.AddIndex(
            model_name="cliente",
            index=models.Index(
                fields=["user", "origem", "criado_em"],
                name="usuarios_cl_user_id_origem_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="cliente",
            index=models.Index(
                fields=["user", "atribuicao_confiavel", "origem"],
                name="usuarios_cl_user_atrib_orig_idx",
            ),
        ),
    ]
