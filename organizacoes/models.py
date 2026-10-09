from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.text import slugify


class Organization(models.Model):
    """Escritório / unidade lógica proprietária dos dados (tenant)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Ativa"
        INACTIVE = "inactive", "Inativa"

    name = models.CharField("Nome", max_length=200)
    slug = models.SlugField("Slug", max_length=220, unique=True, blank=True)
    status = models.CharField(
        "Status",
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="organizations_created",
        verbose_name="Criado por",
    )
    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Organização"
        verbose_name_plural = "Organizações"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._gerar_slug(self.name)
        super().save(*args, **kwargs)

    def _gerar_slug(self, nome: str) -> str:
        base = slugify(nome) or "org"
        slug = base
        n = 2
        qs = Organization.objects.all()
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        while qs.filter(slug=slug).exists():
            slug = f"{base}-{n}"
            n += 1
        return slug


class Membership(models.Model):
    """Relação User ↔ Organization. Não substitui RBAC de módulos."""

    class Role(models.TextChoices):
        OWNER = "owner", "Proprietário"
        MEMBER = "member", "Membro"

    class Status(models.TextChoices):
        ACTIVE = "active", "Ativa"
        INACTIVE = "inactive", "Inativa"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_memberships",
        verbose_name="Usuário",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="Organização",
    )
    role = models.CharField(
        "Papel",
        max_length=16,
        choices=Role.choices,
        default=Role.MEMBER,
    )
    status = models.CharField(
        "Status",
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    created_at = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Vínculo"
        verbose_name_plural = "Vínculos"
        ordering = ["organization", "user"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"],
                name="uniq_organizacoes_membership_user_org",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["organization", "status"]),
        ]

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.role})"


class OrganizationProviderCredential(models.Model):
    """Cofre Organization-owned. Ciphertext opaco; decrypt só no service."""

    class Provider(models.TextChoices):
        GOOGLE_ADS = "google_ads", "Google Ads"
        ASAAS = "asaas", "Asaas"

    class Status(models.TextChoices):
        INACTIVE = "inactive", "Inativa"
        CONFIGURED = "configured", "Configurada"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="provider_credentials",
        db_index=False,
    )
    provider = models.CharField(max_length=32, choices=Provider.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.INACTIVE,
    )
    ciphertext = models.BinaryField()
    key_id = models.CharField(max_length=16, default="v1")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Credencial de provider"
        verbose_name_plural = "Credenciais de provider"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "provider"],
                name="uniq_org_provider_credential",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider}@org-{self.organization_id}"

    def __repr__(self) -> str:
        return (
            f"<OrganizationProviderCredential: {self.provider}"
            f"@org-{self.organization_id}>"
        )
