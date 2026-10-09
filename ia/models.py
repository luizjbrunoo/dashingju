from django.db import models
from usuarios.models import Cliente
from usuarios.models import Documentos

from ia.fields import RagVectorField
from ia.services.embeddings import RAG_EMBEDDING_DIM, RAG_EMBEDDING_MODEL

class Pergunta(models.Model):
    pergunta = models.TextField()
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)

    def __str__(self):
        return self.pergunta

class ContextRag(models.Model):
    content = models.JSONField()
    tool_name = models.CharField(max_length=255)
    tool_args = models.JSONField(null=True, blank=True)
    pergunta = models.ForeignKey(Pergunta, on_delete=models.CASCADE)

    def __str__(self):
        return self.tool_name

class AnaliseJurisprudencia(models.Model):
    documento = models.ForeignKey(Documentos, on_delete=models.CASCADE, related_name='analises')
    indice_risco = models.IntegerField()
    classificacao = models.CharField(max_length=20)  # Baixo, Médio, Alto, Crítico
    erros_coerencia = models.JSONField(default=list)
    riscos_juridicos = models.JSONField(default=list)
    problemas_formatacao = models.JSONField(default=list)
    red_flags = models.JSONField(default=list)
    tempo_processamento = models.IntegerField(default=0)  # em segundos
    data_criacao = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-data_criacao']

    def __str__(self):
        return f"Análise - {self.documento.get_tipo_display()} - {self.data_criacao.strftime('%d/%m/%Y %H:%M')}"


class VectorChunk(models.Model):
    """Índice vetorial tenant-scoped. Texto canónico continua em Documentos.content."""

    organization = models.ForeignKey(
        "organizacoes.Organization",
        on_delete=models.CASCADE,
        related_name="vector_chunks",
    )
    documento = models.ForeignKey(
        Documentos,
        on_delete=models.CASCADE,
        related_name="vector_chunks",
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        related_name="vector_chunks",
    )
    chunk_id = models.CharField(max_length=128)
    text = models.TextField()
    embedding = RagVectorField(dimensions=RAG_EMBEDDING_DIM)
    embedding_model = models.CharField(max_length=64, default=RAG_EMBEDDING_MODEL)
    embedding_dim = models.PositiveIntegerField(default=RAG_EMBEDDING_DIM)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "documento", "chunk_id", "embedding_model"],
                name="uniq_ia_vectorchunk_org_doc_chunk_model",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "documento"],
                name="ia_vectorchunk_org_doc_idx",
            ),
        ]

    def __str__(self):
        return self.chunk_id