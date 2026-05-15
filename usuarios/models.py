from django.contrib.auth.models import User
from django.db import models
from martor.models import MartorField

class Cliente(models.Model):
    TIPO_CHOICES = [
        ('PF', 'Pessoa Fisica'),
        ('PJ', 'Pessoa Juridica'),
    ]
    STATUS_CHOICES = [
        ("em_prospeccao", "Em Prospecção"),
        ("ativo", "Ativo"),
        ("inativo", "Inativo"),
    ]
    FASE_FUNIL_CHOICES = [
        ("primeiro_contato", "Primeiro contato"),
        ("proposta_enviada", "Proposta enviada"),
        ("aguardando_decisao", "Aguardando decisão"),
        ("novo_contato", "Novo contato"),
    ]
    nome = models.CharField(max_length=255)
    email = models.EmailField(max_length=255)
    telefone = models.CharField(max_length=20, blank=True)
    endereco = models.CharField(max_length=255, blank=True)
    tipo = models.CharField(max_length=2, choices=TIPO_CHOICES, default='PF')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="em_prospeccao")
    fase_funil = models.CharField(
        max_length=25,
        choices=FASE_FUNIL_CHOICES,
        default="primeiro_contato",
        blank=True,
    )
    data_relatorio_prospeccao = models.DateField(null=True, blank=True)
    relatorio_prospeccao = models.TextField(blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.nome


class Documentos(models.Model):
    TIPO_CHOICES = [
        ('C', 'Contrato'),
        ('P', 'Petição'),
        ('CONT', 'Contestação'),
        ('R', 'Recursos'),
        ('O', 'Outro'),
    ]
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=255, choices=TIPO_CHOICES, default='O')
    arquivo = models.FileField(upload_to='documentos/')
    data_upload = models.DateTimeField()
    content = MartorField()

    def __str__(self):
        return self.tipo


class Compromisso(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="compromissos")
    titulo = models.CharField(max_length=255)
    descricao = models.TextField(blank=True)
    data_hora = models.DateTimeField()
    data_hora_fim = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["data_hora"]

    def __str__(self):
        return self.titulo


class Tarefa(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tarefas")
    titulo = models.CharField(max_length=255)
    prazo = models.DateField(null=True, blank=True)
    concluida = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["concluida", "prazo", "criado_em"]

    def __str__(self):
        return self.titulo