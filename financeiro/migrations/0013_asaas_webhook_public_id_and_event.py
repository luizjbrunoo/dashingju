import uuid

import django.db.models.deletion
from django.db import migrations, models


def populate_webhook_public_ids(apps, schema_editor):
    OrganizationAsaasSubaccount = apps.get_model(
        "financeiro", "OrganizationAsaasSubaccount"
    )
    for row in OrganizationAsaasSubaccount.objects.filter(webhook_public_id__isnull=True):
        row.webhook_public_id = uuid.uuid4()
        row.save(update_fields=["webhook_public_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0012_organizationfinancialonboarding"),
        ("organizacoes", "0002_organizationprovidercredential"),
    ]

    operations = [
        migrations.AddField(
            model_name="organizationasaassubaccount",
            name="webhook_public_id",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(populate_webhook_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="organizationasaassubaccount",
            name="webhook_public_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.CreateModel(
            name="AsaasWebhookEvent",
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
                ("event_id", models.CharField(max_length=128)),
                ("event_type", models.CharField(max_length=80)),
                ("account_id", models.CharField(blank=True, max_length=64, null=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="asaas_webhook_events",
                        to="organizacoes.organization",
                    ),
                ),
            ],
            options={
                "verbose_name": "Evento webhook Asaas",
                "verbose_name_plural": "Eventos webhook Asaas",
            },
        ),
        migrations.AddIndex(
            model_name="asaaswebhookevent",
            index=models.Index(
                fields=["organization", "created_at"],
                name="fin_asaas_wh_evt_org_cr",
            ),
        ),
        migrations.AddConstraint(
            model_name="asaaswebhookevent",
            constraint=models.UniqueConstraint(
                fields=("organization", "event_id"),
                name="uniq_fin_asaas_webhook_event_org",
            ),
        ),
    ]
