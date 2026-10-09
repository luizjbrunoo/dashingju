from __future__ import annotations

from django.conf import settings
from django.db import models

from .choices import CanalConteudo, ObjetivoConteudo, PlataformaMarketing, StatusConteudo, StatusIdeia, StatusIntegracao, TomComunicacao
from .google_ads_connection import (
    normalize_google_ads_currency_code,
    normalize_google_ads_customer_id,
)


class TenantOwnedModel(models.Model):
    """Ativos de marketing do escritório.

    organization = tenant. usuario = criador / LEGACY_COMPAT. Não é o tenant.
    """

    organization = models.ForeignKey(
        "organizacoes.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="%(class)s_org_marketing",
        db_index=True,
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="%(class)s_marketing",
        help_text="Criador / compatibilidade legada. Não é o tenant.",
    )

    class Meta:
        abstract = True


class ContentProfile(TenantOwnedModel):
    """Perfil de marca/conteúdo do escritório."""

    nome_escritorio = models.CharField(max_length=200, blank=True)
    descricao = models.TextField(blank=True)
    areas_atuacao = models.TextField(
        blank=True,
        help_text="Uma área jurídica por linha.",
    )
    publico = models.TextField(blank=True)
    regiao = models.CharField(max_length=120, blank=True)
    tom_voz = models.CharField(
        max_length=20,
        choices=TomComunicacao.choices,
        default=TomComunicacao.PROFISSIONAL,
        blank=True,
    )
    diferenciais = models.TextField(blank=True)
    palavras_preferidas = models.TextField(blank=True)
    palavras_evitar = models.TextField(blank=True)
    ctas_permitidas = models.TextField(blank=True)
    observacoes_institucionais = models.TextField(blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Perfil de conteúdo"
        verbose_name_plural = "Perfis de conteúdo"
        constraints = [
            models.UniqueConstraint(fields=["usuario"], name="marketing_unique_profile_per_user"),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(organization__isnull=False),
                name="marketing_unique_profile_per_org",
            ),
        ]

    def __str__(self) -> str:
        return self.nome_escritorio or f"Perfil de {self.usuario}"

    def areas_lista(self) -> list[str]:
        return [a.strip() for a in self.areas_atuacao.splitlines() if a.strip()]


class EditorialCalendar(TenantOwnedModel):
    """Plano editorial (container de itens planejados)."""

    nome = models.CharField(max_length=200)
    area_juridica = models.CharField(max_length=120, blank=True)
    publico = models.CharField(max_length=200, blank=True)
    objetivo = models.CharField(
        max_length=30,
        choices=ObjetivoConteudo.choices,
        blank=True,
    )
    periodo_dias = models.PositiveSmallIntegerField(default=30)
    data_inicio = models.DateField(null=True, blank=True)
    data_fim = models.DateField(null=True, blank=True)
    canais = models.CharField(max_length=255, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Calendário editorial"
        verbose_name_plural = "Calendários editoriais"

    def __str__(self) -> str:
        return self.nome


class ContentItem(TenantOwnedModel):
    """Item de conteúdo de marketing jurídico."""

    titulo = models.CharField(max_length=255)
    tema = models.CharField(max_length=255, blank=True)
    area_juridica = models.CharField(max_length=120, blank=True)
    canal = models.CharField(max_length=20, choices=CanalConteudo.choices)
    formato = models.CharField(max_length=40, blank=True)
    objetivo = models.CharField(
        max_length=30,
        choices=ObjetivoConteudo.choices,
        blank=True,
    )
    publico = models.CharField(max_length=200, blank=True)
    palavra_chave = models.CharField(max_length=120, blank=True)
    tom = models.CharField(
        max_length=20,
        choices=TomComunicacao.choices,
        default=TomComunicacao.PROFISSIONAL,
        blank=True,
    )
    cta = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20,
        choices=StatusConteudo.choices,
        default=StatusConteudo.RASCUNHO,
    )
    data_planejada = models.DateField(null=True, blank=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conteudos_responsavel",
    )
    corpo = models.TextField(blank=True)
    metadados = models.JSONField(default=dict, blank=True)
    calendario = models.ForeignKey(
        EditorialCalendar,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="itens",
    )
    origem = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="derivados",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-atualizado_em"]
        verbose_name = "Conteúdo"
        verbose_name_plural = "Conteúdos"
        indexes = [
            models.Index(fields=["usuario", "status"]),
            models.Index(fields=["usuario", "data_planejada"]),
            models.Index(fields=["usuario", "canal"]),
        ]

    def __str__(self) -> str:
        return self.titulo


class ContentVersion(models.Model):
    """Histórico de versões geradas ou editadas."""

    content_item = models.ForeignKey(
        ContentItem,
        on_delete=models.CASCADE,
        related_name="versoes",
    )
    numero = models.PositiveIntegerField()
    corpo_estruturado = models.JSONField(default=dict, blank=True)
    corpo_texto = models.TextField(blank=True)
    gerado_por_ia = models.BooleanField(default=False)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="versoes_conteudo_criadas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-numero"]
        verbose_name = "Versão de conteúdo"
        verbose_name_plural = "Versões de conteúdo"
        constraints = [
            models.UniqueConstraint(
                fields=["content_item", "numero"],
                name="marketing_unique_version_per_item",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.content_item.titulo} v{self.numero}"


class ContentIdea(TenantOwnedModel):
    """Banco de ideias de conteúdo."""

    titulo = models.CharField(max_length=255)
    descricao = models.TextField(blank=True)
    area_juridica = models.CharField(max_length=120, blank=True)
    canal_sugerido = models.CharField(
        max_length=20,
        choices=CanalConteudo.choices,
        blank=True,
    )
    objetivo = models.CharField(
        max_length=30,
        choices=ObjetivoConteudo.choices,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=StatusIdeia.choices,
        default=StatusIdeia.PENDENTE,
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Ideia de conteúdo"
        verbose_name_plural = "Ideias de conteúdo"

    def __str__(self) -> str:
        return self.titulo


class ContentApproval(models.Model):
    """Registro de verificação/revisão (não substitui revisão profissional)."""

    content_item = models.ForeignKey(
        ContentItem,
        on_delete=models.CASCADE,
        related_name="aprovacoes",
    )
    content_version = models.ForeignKey(
        ContentVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="aprovacoes",
    )
    checklist = models.JSONField(default=list, blank=True)
    observacoes = models.TextField(blank=True)
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revisoes_conteudo",
    )
    revisado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-revisado_em"]
        verbose_name = "Revisão de conteúdo"
        verbose_name_plural = "Revisões de conteúdo"

    def __str__(self) -> str:
        return f"Revisão {self.content_item.titulo} ({self.revisado_em:%d/%m/%Y})"


class ContentPerformance(models.Model):
    """Métricas de desempenho (integração futura com plataformas)."""

    content_item = models.OneToOneField(
        ContentItem,
        on_delete=models.CASCADE,
        related_name="performance",
    )
    visualizacoes = models.PositiveIntegerField(null=True, blank=True)
    alcance = models.PositiveIntegerField(null=True, blank=True)
    engajamento = models.PositiveIntegerField(null=True, blank=True)
    cliques = models.PositiveIntegerField(null=True, blank=True)
    leads_atribuidos = models.PositiveIntegerField(null=True, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Desempenho de conteúdo"
        verbose_name_plural = "Desempenhos de conteúdo"

    def __str__(self) -> str:
        return f"Performance: {self.content_item.titulo}"

    @property
    def tem_dados(self) -> bool:
        return any(
            v is not None
            for v in (
                self.visualizacoes,
                self.alcance,
                self.engajamento,
                self.cliques,
                self.leads_atribuidos,
            )
        )


class MarketingIntegracao(TenantOwnedModel):
    """Conexão com plataformas externas (preparado para OAuth/sync futuro)."""

    plataforma = models.CharField(max_length=30, choices=PlataformaMarketing.choices)
    status = models.CharField(
        max_length=20,
        choices=StatusIntegracao.choices,
        default=StatusIntegracao.NAO_CONECTADO,
    )
    metadados = models.JSONField(default=dict, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Integração de marketing"
        verbose_name_plural = "Integrações de marketing"
        permissions = [
            ("view_marketing", "Pode visualizar o painel Google Ads"),
            (
                "view_resultados_marketing",
                "Pode visualizar resultados do negócio (analytics)",
            ),
            ("view_conteudo_marketing", "Pode visualizar marketing de conteúdo"),
            ("edit_conteudo_marketing", "Pode criar e editar conteúdo de marketing"),
            (
                "manage_integracoes_marketing",
                "Pode gerenciar integrações de marketing",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "plataforma"],
                name="marketing_unique_integracao_por_plataforma",
            ),
            models.UniqueConstraint(
                fields=["organization", "plataforma"],
                condition=models.Q(organization__isnull=False),
                name="marketing_unique_integracao_org_plataforma",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.plataforma} ({self.usuario})"


class OrganizationGoogleAdsConnection(models.Model):
    """Estado Organization-owned da conexão Google Ads. Sem secrets."""

    class Status(models.TextChoices):
        DISCONNECTED = "disconnected", "Desconectada"
        AUTHORIZED = "authorized", "Autorizada"
        CONFIGURED = "configured", "Configurada"

    organization = models.OneToOneField(
        "organizacoes.Organization",
        on_delete=models.CASCADE,
        related_name="google_ads_connection",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DISCONNECTED,
    )
    customer_id = models.CharField(max_length=32, blank=True)
    login_customer_id = models.CharField(max_length=32, blank=True)
    descriptive_name = models.CharField(max_length=255, blank=True)
    currency_code = models.CharField(max_length=3, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Conexão Google Ads"
        verbose_name_plural = "Conexões Google Ads"

    def clean(self):
        super().clean()
        self.customer_id = normalize_google_ads_customer_id(self.customer_id)
        self.login_customer_id = normalize_google_ads_customer_id(
            self.login_customer_id
        )
        self.currency_code = normalize_google_ads_currency_code(self.currency_code)
        self.descriptive_name = (self.descriptive_name or "").strip()[:255]

    def save(self, *args, **kwargs):
        self.customer_id = normalize_google_ads_customer_id(self.customer_id)
        self.login_customer_id = normalize_google_ads_customer_id(
            self.login_customer_id
        )
        self.currency_code = normalize_google_ads_currency_code(self.currency_code)
        self.descriptive_name = (self.descriptive_name or "").strip()[:255]
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"google_ads@org-{self.organization_id}"

    def __repr__(self) -> str:
        return (
            f"<OrganizationGoogleAdsConnection: google_ads"
            f"@org-{self.organization_id}>"
        )
