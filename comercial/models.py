"""Models do módulo Comercial — apenas metadados comerciais (não duplica CRM/financeiro)."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class MetaComercial(models.Model):
    """Meta de faturamento do escritório (tenant = Organization).

    usuario = creator / LEGACY_COMPAT. Não determina população tenant.
    """

    organization = models.ForeignKey(
        "organizacoes.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="metas_comerciais",
        db_index=True,
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="metas_comerciais",
        help_text="Criador / compatibilidade legada. Não é o tenant.",
    )
    ano = models.PositiveIntegerField(db_index=True)
    meta_anual = models.DecimalField(max_digits=14, decimal_places=2)
    meta_mensal = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Se vazio no formulário, deriva de meta_anual / 12.",
    )
    ticket_medio = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Ticket usado no cálculo reverso. Null = usar automático.",
    )
    ticket_medio_manual = models.BooleanField(
        default=False,
        help_text="True quando o usuário sobrescreveu o ticket automático.",
    )
    vigencia_inicio = models.DateField()
    vigencia_fim = models.DateField()
    observacoes = models.TextField(blank=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="metas_comerciais_criadas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ano", "-atualizado_em"]
        verbose_name = "Meta comercial"
        verbose_name_plural = "Metas comerciais"
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "ano"],
                name="uniq_comercial_meta_usuario_ano",
            ),
            models.UniqueConstraint(
                fields=["organization", "ano"],
                condition=models.Q(organization__isnull=False),
                name="uniq_comercial_meta_org_ano",
            ),
        ]
        indexes = [
            models.Index(fields=["usuario", "ano"], name="comercial_m_usuario_868787_idx"),
            models.Index(fields=["organization", "ano"], name="comercial_m_organiz_meta_idx"),
            models.Index(
                fields=["usuario", "vigencia_inicio", "vigencia_fim"],
                name="comercial_m_usuario_9a511a_idx",
            ),
        ]
        permissions = [
            ("view_dashboard", "Pode visualizar o painel comercial"),
            ("manage_goals", "Pode criar e editar metas comerciais"),
            ("view_revenue", "Pode visualizar faturamento e gaps"),
            ("view_team", "Pode visualizar desempenho da equipe"),
            ("export_reports", "Pode exportar relatórios comerciais"),
        ]

    def __str__(self):
        dono = self.organization or self.usuario
        return f"Meta {self.ano} — {dono}"

    def clean(self):
        super().clean()
        if self.meta_anual is not None and self.meta_anual <= 0:
            raise ValidationError({"meta_anual": "A meta anual deve ser maior que zero."})
        if self.meta_mensal is not None and self.meta_mensal <= 0:
            raise ValidationError({"meta_mensal": "A meta mensal deve ser maior que zero."})
        if self.ticket_medio is not None and self.ticket_medio <= 0:
            raise ValidationError({"ticket_medio": "O ticket médio deve ser maior que zero."})
        if self.vigencia_inicio and self.vigencia_fim:
            if self.vigencia_fim < self.vigencia_inicio:
                raise ValidationError(
                    {"vigencia_fim": "A vigência final não pode ser anterior ao início."}
                )


class ComercialAuditLog(models.Model):
    """Auditoria de alterações comerciais relevantes."""

    ACAO_META_CRIADA = "meta_criada"
    ACAO_META_ALTERADA = "meta_alterada"
    ACAO_TICKET_MANUAL = "ticket_manual"
    ACAO_RESOLVIDA = "acao_resolvida"
    ACAO_TAREFA_CRIADA = "tarefa_criada"
    ACAO_CHOICES = [
        (ACAO_META_CRIADA, "Meta criada"),
        (ACAO_META_ALTERADA, "Meta alterada"),
        (ACAO_TICKET_MANUAL, "Ticket médio ajustado manualmente"),
        (ACAO_RESOLVIDA, "Ação comercial marcada como resolvida"),
        (ACAO_TAREFA_CRIADA, "Tarefa criada a partir do Comercial"),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comercial_audit_logs",
        help_text="Tenant / escritório dono do dado.",
    )
    ator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="comercial_acoes_auditadas",
    )
    acao = models.CharField(max_length=40, choices=ACAO_CHOICES, db_index=True)
    detalhe = models.TextField(blank=True)
    criado_em = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Auditoria comercial"
        verbose_name_plural = "Auditorias comerciais"
        indexes = [
            models.Index(fields=["usuario", "criado_em"]),
            models.Index(fields=["usuario", "acao"]),
        ]

    def __str__(self):
        return f"{self.acao} @ {self.criado_em}"


class AcaoComercialResolvida(models.Model):
    """Marca recomendações do Comercial como resolvidas (não substitui Agenda/Tarefas).

    Ownership tenant = Organization quando preenchida. usuario = ator / LEGACY_COMPAT.
    """

    organization = models.ForeignKey(
        "organizacoes.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="acoes_comerciais_resolvidas",
        db_index=True,
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="acoes_comerciais_resolvidas",
        help_text="Ator que resolveu / compatibilidade legada. Não é o tenant.",
    )
    chave = models.CharField(
        max_length=120,
        db_index=True,
        help_text="Identificador estável tipo:proposta_sem_followup:123",
    )
    cliente = models.ForeignKey(
        "usuarios.Cliente",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="acoes_comerciais_resolvidas",
    )
    resolvido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acoes_comerciais_marcadas",
    )
    resolvido_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-resolvido_em"]
        verbose_name = "Ação comercial resolvida"
        verbose_name_plural = "Ações comerciais resolvidas"
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "chave"],
                name="uniq_comercial_acao_resolvida_usuario_chave",
            ),
            models.UniqueConstraint(
                fields=["organization", "chave"],
                condition=models.Q(organization__isnull=False),
                name="uniq_comercial_acao_resolvida_org_chave",
            ),
        ]
        indexes = [
            models.Index(
                fields=["usuario", "resolvido_em"],
                name="comercial_a_usuario_182335_idx",
            ),
            models.Index(
                fields=["organization", "resolvido_em"],
                name="comercial_a_organiz_res_idx",
            ),
        ]

    def __str__(self):
        return self.chave


class InvestimentoMidia(models.Model):
    """Investimento de marketing registrado manualmente (sem inventar ROI).

    Ownership tenant = Organization. usuario = criador / LEGACY_COMPAT.
    """

    organization = models.ForeignKey(
        "organizacoes.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="investimentos_midia",
        db_index=True,
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="investimentos_midia",
        help_text="Criador / compatibilidade legada. Não é o tenant.",
    )
    data_inicio = models.DateField()
    data_fim = models.DateField()
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    canal = models.CharField(
        max_length=40,
        blank=True,
        help_text="Opcional: google_ads, instagram, etc. Vazio = geral.",
    )
    observacoes = models.TextField(blank=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="investimentos_midia_criados",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data_fim", "-id"]
        verbose_name = "Investimento de mídia"
        verbose_name_plural = "Investimentos de mídia"
        indexes = [
            models.Index(fields=["usuario", "data_inicio", "data_fim"]),
        ]

    def __str__(self):
        return f"R$ {self.valor} ({self.data_inicio}–{self.data_fim})"

    def clean(self):
        super().clean()
        if self.valor is not None and self.valor <= 0:
            raise ValidationError({"valor": "O valor deve ser maior que zero."})
        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            raise ValidationError(
                {"data_fim": "A data final não pode ser anterior ao início."}
            )


class AdvGrowthScoreSnapshot(models.Model):
    """Histórico diário do ADV Growth Score (critérios objetivos)."""

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="adv_growth_snapshots",
    )
    data_ref = models.DateField(db_index=True)
    score = models.PositiveSmallIntegerField()
    aquisicao = models.PositiveSmallIntegerField()
    atendimento = models.PositiveSmallIntegerField()
    conversao = models.PositiveSmallIntegerField()
    gestao = models.PositiveSmallIntegerField()
    financeiro = models.PositiveSmallIntegerField()
    dados = models.PositiveSmallIntegerField()
    detalhe = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data_ref"]
        verbose_name = "ADV Growth Score"
        verbose_name_plural = "ADV Growth Scores"
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "data_ref"],
                name="uniq_comercial_adv_score_usuario_data",
            )
        ]
        indexes = [
            models.Index(fields=["usuario", "data_ref"]),
        ]

    def __str__(self):
        return f"{self.score}/100 @ {self.data_ref}"
