from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):

    dependencies = [
        ("ia", "0003_analisejurisprudencia"),
    ]

    operations = [
        VectorExtension(),
    ]
