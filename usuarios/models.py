from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from martor.models import MartorField

from .choices import (
    OrigemLead,
    OrigemLeadConsulta,
    Prioridade,
    Recorrencia,
    StatusCompromisso,
    StatusConfirmacaoConsulta,
    StatusTarefa,
    TipoCompromisso,
)
from .services.document_storage import documento_upload_to


class Cliente(models.Model):
    TIPO_CHOICES = [
        ("PF", "Pessoa Fisica"),
        ("PJ", "Pessoa Juridica"),
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
    tipo = models.CharField(max_length=2, choices=TIPO_CHOICES, default="PF")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="em_prospeccao")
    fase_funil = models.CharField(
        max_length=25,
        choices=FASE_FUNIL_CHOICES,
        default="primeiro_contato",
        blank=True,
    )
    data_relatorio_prospeccao = models.DateField(null=True, blank=True)
    relatorio_prospeccao = models.TextField(blank=True)
    origem = models.CharField(
        max_length=30,
        choices=OrigemLead.choices,
        blank=True,
        default=OrigemLead.NAO_IDENTIFICADA,
        db_index=True,
    )
    atribuicao_confiavel = models.BooleanField(
        default=False,
        help_text="True quando a origem foi determinada com critérios objetivos.",
    )
    utm_source = models.CharField(max_length=120, blank=True)
    utm_medium = models.CharField(max_length=120, blank=True)
    utm_campaign = models.CharField(max_length=120, blank=True, db_index=True)
    utm_content = models.CharField(max_length=120, blank=True)
    utm_term = models.CharField(max_length=120, blank=True)
    gclid = models.CharField(max_length=255, blank=True, db_index=True)
    campaign_id = models.CharField(max_length=64, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    organization = models.ForeignKey(
        "organizacoes.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clientes",
        db_index=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["user", "origem", "criado_em"]),
            models.Index(fields=["user", "atribuicao_confiavel", "origem"]),
            models.Index(
                fields=["user", "origem", "atribuicao_confiavel", "criado_em"],
                name="usuarios_cl_mkt_leads_idx",
            ),
            models.Index(
                fields=["user", "fase_funil"],
                name="usuarios_cl_fase_funil_idx",
            ),
        ]

    def __str__(self):
        return self.nome

    @property
    def origem_google_ads_atribuida(self) -> bool:
        """Lead contabilizável como Google Ads (origem confiável)."""
        return (
            self.origem == OrigemLead.GOOGLE_ADS and self.atribuicao_confiavel
        )


class Documentos(models.Model):
    TIPO_CHOICES = [
        ("C", "Contrato"),
        ("P", "Petição"),
        ("CONT", "Contestação"),
        ("R", "Recursos"),
        ("O", "Outro"),
    ]
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=255, choices=TIPO_CHOICES, default="O")
    arquivo = models.FileField(upload_to=documento_upload_to)
    data_upload = models.DateTimeField()
    content = MartorField()

    def __str__(self):
        return self.tipo


class Compromisso(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="compromissos",
    )
    organization = models.ForeignKey(
        "organizacoes.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="compromissos",
        db_index=True,
    )
    titulo = models.CharField(max_length=255)
    descricao = models.TextField(blank=True)
    tipo = models.CharField(
        max_length=30,
        choices=TipoCompromisso.choices,
        default=TipoCompromisso.OUTRO,
    )
    status = models.CharField(
        max_length=20,
        choices=StatusCompromisso.choices,
        default=StatusCompromisso.AGENDADO,
    )
    prioridade = models.CharField(
        max_length=10,
        choices=Prioridade.choices,
        default=Prioridade.NORMAL,
    )
    data_hora = models.DateTimeField()
    data_hora_fim = models.DateTimeField(null=True, blank=True)
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="compromissos",
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="compromissos_responsavel",
    )
    processo_referencia = models.CharField(
        max_length=64,
        blank=True,
        help_text="Número CNJ ou referência do processo (até existir model dedicado).",
    )
    prazo_oficial = models.DateField(null=True, blank=True)
    prazo_interno = models.DateField(null=True, blank=True)
    area_juridica = models.CharField(max_length=120, blank=True)
    confirmacao_consulta = models.CharField(
        max_length=20,
        choices=StatusConfirmacaoConsulta.choices,
        blank=True,
        default="",
        help_text="Status de confirmação para consultas.",
    )
    origem_lead = models.CharField(
        max_length=30,
        choices=OrigemLeadConsulta.choices,
        blank=True,
        default="",
        help_text="Origem do lead para consultas.",
    )
    recorrencia = models.CharField(
        max_length=20,
        choices=Recorrencia.choices,
        default=Recorrencia.NAO_REPETIR,
    )
    lembrete_minutos = models.PositiveSmallIntegerField(null=True, blank=True)
    metadados = models.JSONField(default=dict, blank=True)
    cancelado_em = models.DateTimeField(null=True, blank=True)
    motivo_cancelamento = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["data_hora"]
        permissions = [
            ("view_agenda", "Pode visualizar a agenda"),
            ("create_agenda", "Pode criar compromissos e tarefas"),
            ("edit_agenda", "Pode editar compromissos e tarefas"),
            ("cancel_agenda", "Pode cancelar compromissos e tarefas"),
            ("view_audit_agenda", "Pode visualizar auditoria da agenda"),
        ]
        indexes = [
            models.Index(fields=["user", "data_hora"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "tipo"]),
            models.Index(fields=["responsavel", "data_hora"]),
            models.Index(fields=["cliente"]),
            models.Index(fields=["user", "prazo_interno"]),
            models.Index(fields=["user", "confirmacao_consulta"]),
            models.Index(
                fields=["user", "tipo", "status", "data_hora"],
                name="usuarios_co_mkt_idx",
            ),
            models.Index(
                fields=["user", "cliente", "data_hora", "status"],
                name="usuarios_co_cli_futuro_idx",
            ),
        ]

    def __str__(self):
        return self.titulo

    @property
    def ativo(self) -> bool:
        return self.status != StatusCompromisso.CANCELADO

    def cancelar(self, motivo: str = "") -> None:
        self.status = StatusCompromisso.CANCELADO
        self.cancelado_em = timezone.now()
        self.motivo_cancelamento = motivo
        self.save(update_fields=["status", "cancelado_em", "motivo_cancelamento", "atualizado_em"])

    @property
    def prazo_interno_vencido(self) -> bool:
        from usuarios.services.compromisso_prazos import prazo_interno_vencido

        return prazo_interno_vencido(self)

    @property
    def prazo_oficial_vencido(self) -> bool:
        from usuarios.services.compromisso_prazos import prazo_oficial_vencido

        return prazo_oficial_vencido(self)

    @property
    def dias_ate_prazo_interno(self) -> int | None:
        from usuarios.services.compromisso_prazos import dias_ate_prazo_interno

        return dias_ate_prazo_interno(self)

    def clean(self):
        super().clean()
        if self.organization_id and self.cliente_id:
            cliente = self.cliente
            if cliente is None or cliente.organization_id != self.organization_id:
                raise ValidationError(
                    {"cliente": "Cliente não pertence ao escritório do compromisso."}
                )
        if self.organization_id and self.responsavel_id:
            from usuarios.services.agenda_equipe import responsavel_permitido

            if not responsavel_permitido(self.organization, self.responsavel):
                raise ValidationError(
                    {"responsavel": "Responsável não pertence ao escritório."}
                )
        if self.tipo == TipoCompromisso.PRAZO:
            if (
                self.prazo_interno
                and self.prazo_oficial
                and self.prazo_interno > self.prazo_oficial
            ):
                raise ValidationError(
                    {
                        "prazo_interno": (
                            "O prazo interno deve ser anterior ou igual ao prazo oficial."
                        )
                    }
                )


class CompromissoParticipante(models.Model):
    compromisso = models.ForeignKey(
        Compromisso,
        on_delete=models.CASCADE,
        related_name="participantes",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="compromissos_participante",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["compromisso", "usuario"],
                name="usuarios_unique_participante_compromisso",
            ),
        ]

    def __str__(self):
        return f"{self.usuario} em {self.compromisso}"


class Tarefa(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tarefas",
    )
    organization = models.ForeignKey(
        "organizacoes.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tarefas",
        db_index=True,
    )
    titulo = models.CharField(max_length=255)
    descricao = models.TextField(blank=True)
    prazo = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=StatusTarefa.choices,
        default=StatusTarefa.PENDENTE,
    )
    prioridade = models.CharField(
        max_length=10,
        choices=Prioridade.choices,
        default=Prioridade.NORMAL,
    )
    concluida = models.BooleanField(default=False)
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tarefas",
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tarefas_responsavel",
    )
    processo_referencia = models.CharField(max_length=64, blank=True)
    metadados = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "prazo", "criado_em"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "prazo"]),
            models.Index(fields=["responsavel", "prazo"]),
            models.Index(fields=["cliente"]),
            models.Index(
                fields=["user", "cliente", "status", "prazo"],
                name="usuarios_ta_mkt_acao_idx",
            ),
        ]

    def __str__(self):
        return self.titulo

    @property
    def atrasada(self) -> bool:
        if not self.prazo:
            return False
        if self.status in (StatusTarefa.CONCLUIDA, StatusTarefa.CANCELADA):
            return False
        return self.prazo < timezone.localdate()

    @property
    def dias_atraso(self) -> int:
        if not self.atrasada:
            return 0
        return (timezone.localdate() - self.prazo).days

    def save(self, *args, **kwargs):
        self.concluida = self.status == StatusTarefa.CONCLUIDA
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.organization_id and self.cliente_id:
            cliente = self.cliente
            if cliente is None or cliente.organization_id != self.organization_id:
                raise ValidationError(
                    {"cliente": "Cliente não pertence ao escritório da tarefa."}
                )
        if self.organization_id and self.responsavel_id:
            from usuarios.services.agenda_equipe import responsavel_permitido

            if not responsavel_permitido(self.organization, self.responsavel):
                raise ValidationError(
                    {"responsavel": "Responsável não pertence ao escritório."}
                )


class AgendaLembrete(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agenda_lembretes",
    )
    compromisso = models.ForeignKey(
        Compromisso,
        on_delete=models.CASCADE,
        related_name="lembretes_disparados",
    )
    titulo = models.CharField(max_length=255)
    mensagem = models.TextField(blank=True)
    lido = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["user", "lido", "criado_em"]),
        ]

    def __str__(self):
        return self.titulo


class AgendaAuditLog(models.Model):
    class Acao(models.TextChoices):
        CRIADO = "criado", "Criado"
        ALTERADO = "alterado", "Alterado"
        CANCELADO = "cancelado", "Cancelado"
        CONCLUIDO = "concluido", "Concluído"
        RESPONSAVEL_ALTERADO = "responsavel_alterado", "Responsável alterado"
        PRAZO_ALTERADO = "prazo_alterado", "Prazo alterado"

    class ItemTipo(models.TextChoices):
        COMPROMISSO = "compromisso", "Compromisso"
        TAREFA = "tarefa", "Tarefa"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="agenda_audit_logs",
    )
    item_tipo = models.CharField(max_length=20, choices=ItemTipo.choices)
    item_id = models.PositiveIntegerField()
    acao = models.CharField(max_length=30, choices=Acao.choices)
    campo = models.CharField(max_length=60, blank=True)
    valor_anterior = models.TextField(blank=True)
    valor_novo = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["item_tipo", "item_id"]),
            models.Index(fields=["usuario", "criado_em"]),
        ]

    def __str__(self):
        return f"{self.item_tipo} #{self.item_id} — {self.acao}"
