import django.db.models.deletion
from django.db import migrations, models

import ia.fields


class Migration(migrations.Migration):

    dependencies = [
        ("ia", "0004_vector_extension"),
        ("organizacoes", "0001_initial"),
        ("usuarios", "0021_document_arquivo_tenant_upload_to"),
    ]

    operations = [
        migrations.CreateModel(
            name="VectorChunk",
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
                ("chunk_id", models.CharField(max_length=128)),
                ("text", models.TextField()),
                (
                    "embedding",
                    ia.fields.RagVectorField(dimensions=1536),
                ),
                (
                    "embedding_model",
                    models.CharField(default="text-embedding-3-small", max_length=64),
                ),
                ("embedding_dim", models.PositiveIntegerField(default=1536)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "cliente",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vector_chunks",
                        to="usuarios.cliente",
                    ),
                ),
                (
                    "documento",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vector_chunks",
                        to="usuarios.documentos",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vector_chunks",
                        to="organizacoes.organization",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="vectorchunk",
            index=models.Index(
                fields=["organization", "documento"],
                name="ia_vectorchunk_org_doc_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="vectorchunk",
            constraint=models.UniqueConstraint(
                fields=["organization", "documento", "chunk_id", "embedding_model"],
                name="uniq_ia_vectorchunk_org_doc_chunk_model",
            ),
        ),
    ]
