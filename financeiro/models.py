import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from .choices import (
    AcaoCobrancaHistorico,
    CategoriaCobranca,
    FormaPagamento,
    StatusCobranca,
    StatusContrato,
)
from .services.cobrancas import saldo_cobranca, sincronizar_status_cobranca, total_recebido


class Banco(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bancos_financeiro",
    )
    nome = models.CharField(max_length=120)
    agencia = models.CharField(max_length=20, blank=True)
    conta = models.CharField(max_length=30, blank=True)
    saldo_inicial = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0"), blank=True
    )

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
        if self.usuario_id and self.banco_id and self.categoria_id:
            if self.banco.usuario_id != self.usuario_id:
                raise ValidationError("O banco não pertence ao usuário.")
            if self.categoria.usuario_id != self.usuario_id:
                raise ValidationError("A categoria não pertence ao usuário.")


class Contrato(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="contratos_financeiro",
    )
    cliente = models.ForeignKey(
        "usuarios.Cliente",
        on_delete=models.PROTECT,
        related_name="contratos",
    )
    referencia = models.CharField(max_length=64)
    descricao = models.CharField(max_length=255)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=StatusContrato.choices,
        default=StatusContrato.DRAFT,
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contratos_responsavel",
    )
    observacoes = models.TextField(blank=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contratos_criados",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Contrato"
        verbose_name_plural = "Contratos"
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "referencia"],
                name="uniq_financeiro_contrato_usuario_referencia",
            )
        ]
        indexes = [
            models.Index(fields=["usuario", "cliente"]),
            models.Index(fields=["usuario", "status"]),
            models.Index(
                fields=["usuario", "status", "criado_em"],
                name="financeiro_contr_mkt_idx",
            ),
        ]

    def __str__(self):
        return f"{self.referencia} — {self.cliente}"

    @property
    def rotulo(self) -> str:
        return self.referencia

    def clean(self):
        super().clean()
        if self.valor_total is not None and self.valor_total <= 0:
            raise ValidationError({"valor_total": "O valor deve ser maior que zero."})
        if self.usuario_id and self.cliente_id:
            if self.cliente.user_id != self.usuario_id:
                raise ValidationError({"cliente": "Cliente não pertence ao usuário."})


class Cobranca(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cobrancas_financeiro",
    )
    cliente = models.ForeignKey(
        "usuarios.Cliente",
        on_delete=models.PROTECT,
        related_name="cobrancas",
    )
    contrato = models.ForeignKey(
        Contrato,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cobrancas",
    )
    contrato_referencia = models.CharField(
        max_length=64,
        blank=True,
        help_text="Cópia da referência do contrato para exibição e busca.",
    )
    descricao = models.CharField(max_length=255)
    valor_original = models.DecimalField(max_digits=14, decimal_places=2)
    data_vencimento = models.DateField()
    categoria = models.CharField(
        max_length=30,
        choices=CategoriaCobranca.choices,
        default=CategoriaCobranca.HONORARIOS,
    )
    status = models.CharField(
        max_length=20,
        choices=StatusCobranca.choices,
        default=StatusCobranca.PENDING,
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cobrancas_responsavel",
    )
    forma_prevista_pagamento = models.CharField(
        max_length=20,
        choices=FormaPagamento.choices,
        blank=True,
    )
    observacoes_internas = models.TextField(blank=True)
    grupo_parcelamento_id = models.UUIDField(null=True, blank=True, db_index=True)
    parcela_numero = models.PositiveSmallIntegerField(null=True, blank=True)
    parcela_total = models.PositiveSmallIntegerField(null=True, blank=True)
    cancelado_em = models.DateTimeField(null=True, blank=True)
    motivo_cancelamento = models.TextField(blank=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cobrancas_criadas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["data_vencimento", "id"]
        verbose_name = "Cobrança"
        verbose_name_plural = "Cobranças"
        permissions = [
            ("view_cobrancas", "Pode visualizar cobranças"),
            ("create_cobrancas", "Pode criar cobranças"),
            ("edit_cobrancas", "Pode editar cobranças"),
            ("cancel_cobrancas", "Pode cancelar cobranças"),
            ("view_relatorios_cobrancas", "Pode visualizar relatórios de cobranças"),
        ]
        indexes = [
            models.Index(fields=["usuario", "status", "data_vencimento"]),
            models.Index(fields=["usuario", "cliente"]),
            models.Index(fields=["usuario", "data_vencimento"]),
            models.Index(fields=["responsavel", "data_vencimento"]),
        ]

    def __str__(self):
        return f"{self.descricao} — {self.cliente}"

    @property
    def total_recebido(self) -> Decimal:
        return total_recebido(self)

    @property
    def saldo(self) -> Decimal:
        return saldo_cobranca(self)

    @property
    def ativa(self) -> bool:
        return self.status != StatusCobranca.CANCELED

    @property
    def parcela_rotulo(self) -> str:
        if self.parcela_numero and self.parcela_total:
            return f"Parcela {self.parcela_numero}/{self.parcela_total}"
        return ""

    def atualizar_status(self, *, salvar: bool = True) -> str:
        return sincronizar_status_cobranca(self, salvar=salvar)

    def cancelar(self, motivo: str = "") -> None:
        self.status = StatusCobranca.CANCELED
        self.cancelado_em = timezone.now()
        self.motivo_cancelamento = motivo
        self.save(
            update_fields=[
                "status",
                "cancelado_em",
                "motivo_cancelamento",
                "atualizado_em",
            ]
        )

    def clean(self):
        super().clean()
        if self.valor_original is not None and self.valor_original <= 0:
            raise ValidationError({"valor_original": "O valor deve ser maior que zero."})
        if self.usuario_id and self.cliente_id:
            if self.cliente.user_id != self.usuario_id:
                raise ValidationError({"cliente": "Cliente não pertence ao usuário."})
        if self.contrato_id and self.usuario_id:
            if self.contrato.usuario_id != self.usuario_id:
                raise ValidationError({"contrato": "Contrato não pertence ao usuário."})
            if self.cliente_id and self.contrato.cliente_id != self.cliente_id:
                raise ValidationError({"contrato": "Contrato não pertence ao cliente."})
        if self.parcela_numero and self.parcela_total:
            if self.parcela_numero > self.parcela_total:
                raise ValidationError(
                    {"parcela_numero": "Número da parcela não pode exceder o total."}
                )


class CobrancaRecebimento(models.Model):
    cobranca = models.ForeignKey(
        Cobranca,
        on_delete=models.CASCADE,
        related_name="recebimentos",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recebimentos_cobranca",
    )
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    data_recebimento = models.DateField()
    forma_pagamento = models.CharField(
        max_length=20,
        choices=FormaPagamento.choices,
        default=FormaPagamento.PIX,
    )
    referencia = models.CharField(max_length=120, blank=True)
    observacao = models.TextField(blank=True)
    movimento = models.ForeignKey(
        Movimento,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recebimentos_cobranca",
        help_text="Vínculo opcional com receita realizada no extrato.",
    )
    cancelado_em = models.DateTimeField(null=True, blank=True)
    motivo_estorno = models.TextField(blank=True)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recebimentos_registrados",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data_recebimento", "-id"]
        verbose_name = "Recebimento de cobrança"
        verbose_name_plural = "Recebimentos de cobrança"
        permissions = [
            ("view_recebimentos", "Pode visualizar recebimentos"),
            ("create_recebimentos", "Pode registrar recebimentos"),
        ]
        indexes = [
            models.Index(fields=["usuario", "data_recebimento"]),
            models.Index(fields=["cobranca", "cancelado_em"]),
            models.Index(
                fields=["usuario", "cancelado_em", "data_recebimento"],
                name="financeiro_receb_mkt_idx",
            ),
        ]

    def __str__(self):
        return f"R$ {self.valor} em {self.data_recebimento}"

    @property
    def ativo(self) -> bool:
        return self.cancelado_em is None

    def estornar(self, motivo: str = "") -> None:
        self.cancelado_em = timezone.now()
        self.motivo_estorno = motivo
        self.save(update_fields=["cancelado_em", "motivo_estorno"])

    def clean(self):
        super().clean()
        if self.valor is not None and self.valor <= 0:
            raise ValidationError({"valor": "O valor recebido deve ser maior que zero."})
        if self.cobranca_id and self.usuario_id:
            if self.cobranca.usuario_id != self.usuario_id:
                raise ValidationError("A cobrança não pertence ao usuário.")
        if self.movimento_id and self.usuario_id:
            if self.movimento.usuario_id != self.usuario_id:
                raise ValidationError("O movimento não pertence ao usuário.")


class CobrancaHistorico(models.Model):
    cobranca = models.ForeignKey(
        Cobranca,
        on_delete=models.CASCADE,
        related_name="historico",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="historico_cobrancas",
    )
    acao = models.CharField(max_length=30, choices=AcaoCobrancaHistorico.choices)
    descricao = models.TextField(blank=True)
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acoes_historico_cobranca",
    )
    metadados = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Histórico de cobrança"
        verbose_name_plural = "Históricos de cobrança"
        indexes = [
            models.Index(fields=["cobranca", "criado_em"]),
            models.Index(fields=["usuario", "criado_em"]),
        ]

    def __str__(self):
        return f"{self.cobranca_id} — {self.get_acao_display()}"


def novo_grupo_parcelamento() -> uuid.UUID:
    return uuid.uuid4()
