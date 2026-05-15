from django.db import migrations, models


def migrate_status_to_text(apps, schema_editor):
    Cliente = apps.get_model("usuarios", "Cliente")
    for cliente in Cliente.objects.all().only("id", "status", "status_novo"):
        cliente.status_novo = "ativo" if cliente.status else "inativo"
        cliente.save(update_fields=["status_novo"])


class Migration(migrations.Migration):
    dependencies = [
        ("usuarios", "0004_compromisso_data_hora_fim"),
    ]

    operations = [
        migrations.AddField(
            model_name="cliente",
            name="status_novo",
            field=models.CharField(
                choices=[
                    ("em_prospeccao", "Em Prospecção"),
                    ("ativo", "Ativo"),
                    ("inativo", "Inativo"),
                ],
                default="em_prospeccao",
                max_length=20,
            ),
        ),
        migrations.RunPython(migrate_status_to_text, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="cliente",
            name="status",
        ),
        migrations.RenameField(
            model_name="cliente",
            old_name="status_novo",
            new_name="status",
        ),
    ]
