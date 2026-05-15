from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum


class Banco(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bancos_financeiro",
    )
    nome = models.CharField(max_length=120)
    agencia = models.CharField(max_length=20, blank=True)
    conta = models.CharField(max_length=30, blank=True)
    saldo_inicial = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["nome"]
        verbose_name = "Banco"
        verbose_name_plural = "Bancos"

    def __str__(self):
        return self.nome

    def saldo_atual(self) -> Decimal:
        agg = self.movimento_set.aggregate(
            receitas=Sum("valor", filter=models.Q(categoria__tipo=Categoria.Tipo.RECEITA)),
            despesas=Sum("valor", filter=models.Q(categoria__tipo=Categoria.Tipo.DESPESA)),
        )
        r = agg["receitas"] or Decimal("0")
        d = agg["despesas"] or Decimal("0")
        return self.saldo_inicial + r - d


class Categoria(models.Model):
    class Tipo(models.TextChoices):
        RECEITA = "receita", "Receita"
        DESPESA = "despesa", "Despesa"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="categorias_financeiro",
    )
    nome = models.CharField(max_length=120)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)

    class Meta:
        ordering = ["tipo", "nome"]
        verbose_name = "Categoria"
        verbose_name_plural = "Categorias"
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "nome", "tipo"],
                name="uniq_financeiro_categoria_usuario_nome_tipo",
            )
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.nome}"


class Movimento(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="movimentos_financeiro",
    )
    banco = models.ForeignKey(Banco, on_delete=models.CASCADE)
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT)
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    data = models.DateField()
    descricao = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data", "-id"]
        verbose_name = "Movimento"
        verbose_name_plural = "Movimentos"

    def __str__(self):
        return f"{self.data} {self.categoria} {self.valor}"

    def clean(self):
        super().clean()
        if self.valor is not None and self.valor <= 0:
            raise ValidationError({"valor": "O valor deve ser maior que zero."})
        if self.banco_id and self.categoria_id:
            if self.banco.usuario_id != self.usuario_id:
                raise ValidationError("O banco não pertence ao usuário.")
            if self.categoria.usuario_id != self.usuario_id:
                raise ValidationError("A categoria não pertence ao usuário.")
