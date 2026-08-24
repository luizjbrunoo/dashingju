from datetime import datetime, timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from .choices import (
    EscopoAgenda,
    LembreteMinutos,
    ModalidadeAudiencia,
    OrigemLeadConsulta,
    Recorrencia,
    StatusCompromisso,
    StatusConfirmacaoConsulta,
    StatusTarefa,
    TipoCompromisso,
)
from .models import Cliente, Compromisso, CompromissoParticipante, Tarefa
from .services.agenda_equipe import (
    membros_agenda,
    participantes_permitidos,
    processos_distintos,
    responsavel_permitido,
)
from .services.compromisso_metadados import (
    aplicar_metadados_por_tipo,
    extrair_audiencia,
    extrair_followup,
)

User = get_user_model()

_INPUT = (
    "w-full rounded-xl border border-zinc-700 bg-zinc-950/50 px-3 py-2 text-sm "
    "text-zinc-100 focus:border-bally/50 focus:ring-2 focus:ring-bally/30 outline-none"
)
_SELECT = _INPUT
_DATE = f"{_INPUT} [color-scheme:dark]"


class CompromissoForm(forms.ModelForm):
    data = forms.DateField(
        label="Data",
        widget=forms.DateInput(attrs={"type": "date", "class": _DATE}),
    )
    hora_inicio = forms.TimeField(
        label="Horário inicial",
        widget=forms.TimeInput(attrs={"type": "time", "class": _DATE}),
    )
    hora_fim = forms.TimeField(
        label="Horário final",
        widget=forms.TimeInput(attrs={"type": "time", "class": _DATE}),
    )
    participantes = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Participantes",
        widget=forms.SelectMultiple(attrs={"class": _SELECT, "size": 3}),
    )
    prazo_oficial = forms.DateField(
        required=False,
        label="Prazo oficial",
        widget=forms.DateInput(attrs={"type": "date", "class": _DATE}),
    )
    prazo_interno = forms.DateField(
        required=False,
        label="Prazo interno",
        help_text="Data-alvo da equipe (antes ou no prazo oficial).",
        widget=forms.DateInput(attrs={"type": "date", "class": _DATE}),
    )
    modalidade_audiencia = forms.ChoiceField(
        required=False,
        label="Modalidade",
        choices=[("", "Selecione")] + list(ModalidadeAudiencia.choices),
        widget=forms.Select(attrs={"class": _SELECT}),
    )
    local_audiencia = forms.CharField(
        required=False,
        label="Local / fórum",
        max_length=255,
        widget=forms.TextInput(
            attrs={"class": _INPUT, "placeholder": "Ex.: 1ª Vara Cível — Fórum Central"}
        ),
    )
    link_audiencia = forms.URLField(
        required=False,
        label="Link da videoconferência",
        widget=forms.URLInput(
            attrs={"class": _INPUT, "placeholder": "https://...", "autocomplete": "off"}
        ),
    )
    vara_audiencia = forms.CharField(
        required=False,
        label="Vara / turma",
        max_length=120,
        widget=forms.TextInput(attrs={"class": _INPUT, "placeholder": "Opcional"}),
    )
    tribunal_audiencia = forms.CharField(
        required=False,
        label="Tribunal",
        max_length=120,
        widget=forms.TextInput(
            attrs={"class": _INPUT, "placeholder": "Ex.: TRT5, TJSP, STJ"}
        ),
    )
    observacoes_audiencia = forms.CharField(
        required=False,
        label="Observações",
        widget=forms.Textarea(attrs={"class": _INPUT, "rows": 2, "placeholder": "Opcional"}),
    )
    confirmacao_consulta = forms.ChoiceField(
        required=False,
        label="Confirmação",
        choices=[("", "Não informado")] + list(StatusConfirmacaoConsulta.choices),
        widget=forms.Select(attrs={"class": _SELECT}),
    )
    origem_lead = forms.ChoiceField(
        required=False,
        label="Origem do lead",
        choices=[("", "Não informado")] + list(OrigemLeadConsulta.choices),
        widget=forms.Select(attrs={"class": _SELECT}),
    )
    followup_observacao = forms.CharField(
        required=False,
        label="Observação",
        widget=forms.Textarea(attrs={"class": _INPUT, "rows": 2, "placeholder": "Opcional"}),
    )
    followup_oportunidade = forms.CharField(
        required=False,
        label="Oportunidade / referência",
        max_length=120,
        help_text="Referência textual até existir model de oportunidade no CRM.",
        widget=forms.TextInput(
            attrs={"class": _INPUT, "placeholder": "Ex.: Proposta honorários — Empresa Delta"}
        ),
    )
    lembrete_minutos = forms.TypedChoiceField(
        required=False,
        label="Lembrete",
        choices=[("", "Sem lembrete")] + list(LembreteMinutos.choices),
        coerce=lambda v: int(v) if v not in (None, "") else None,
        empty_value=None,
        widget=forms.Select(attrs={"class": _SELECT}),
    )

    class Meta:
        model = Compromisso
        fields = [
            "titulo",
            "tipo",
            "status",
            "prioridade",
            "recorrencia",
            "cliente",
            "processo_referencia",
            "area_juridica",
            "descricao",
            "responsavel",
        ]
        widgets = {
            "titulo": forms.TextInput(attrs={"class": _INPUT}),
            "tipo": forms.Select(attrs={"class": _SELECT}),
            "status": forms.Select(attrs={"class": _SELECT}),
            "prioridade": forms.Select(attrs={"class": _SELECT}),
            "recorrencia": forms.Select(attrs={"class": _SELECT}),
            "cliente": forms.Select(attrs={"class": _SELECT}),
            "processo_referencia": forms.TextInput(
                attrs={
                    "class": _INPUT,
                    "placeholder": "0000000-00.0000.0.00.0000",
                    "autocomplete": "off",
                }
            ),
            "area_juridica": forms.TextInput(
                attrs={
                    "class": _INPUT,
                    "placeholder": "Ex.: Direito Trabalhista",
                    "autocomplete": "off",
                }
            ),
            "descricao": forms.Textarea(attrs={"class": _INPUT, "rows": 3}),
            "responsavel": forms.Select(attrs={"class": _SELECT}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.auto_id = "compromisso_%s"
        if user:
            membros = membros_agenda(user)
            self.fields["responsavel"].queryset = membros
            if not self.instance.pk:
                self.fields["responsavel"].initial = user.pk
            self.fields["responsavel"].empty_label = None
            self.fields["participantes"].queryset = membros
            self.fields["cliente"].queryset = Cliente.objects.filter(user=user).order_by(
                "nome"
            )
        self.fields["cliente"].required = False
        self.fields["cliente"].empty_label = "Nenhum"
        self.fields["processo_referencia"].required = False
        cliente_id = None
        if self.data.get("cliente"):
            try:
                cliente_id = int(self.data.get("cliente"))
            except (TypeError, ValueError):
                cliente_id = None
        elif self.instance.pk and self.instance.cliente_id:
            cliente_id = self.instance.cliente_id
        sugestoes = processos_distintos(user, cliente_id=cliente_id) if user else []
        datalist_id = "processos-compromisso-sugeridos"
        self.fields["processo_referencia"].widget.attrs["list"] = datalist_id
        self.fields["processo_referencia"].widget.attrs["data-datalist"] = datalist_id
        self._processos_datalist_id = datalist_id
        self.processos_sugeridos = sugestoes
        self.fields["recorrencia"].required = False
        if not self.instance.pk and not self.initial.get("recorrencia"):
            self.fields["recorrencia"].initial = Recorrencia.NAO_REPETIR
        self.fields["status"].choices = [
            choice
            for choice in StatusCompromisso.choices
            if choice[0] != StatusCompromisso.CANCELADO
        ]
        if self.instance.pk:
            dt = timezone.localtime(self.instance.data_hora)
            self.fields["data"].initial = dt.date()
            self.fields["hora_inicio"].initial = dt.time().replace(second=0, microsecond=0)
            if self.instance.data_hora_fim:
                dt_fim = timezone.localtime(self.instance.data_hora_fim)
                self.fields["hora_fim"].initial = dt_fim.time().replace(
                    second=0, microsecond=0
                )
            self.fields["prazo_oficial"].initial = self.instance.prazo_oficial
            self.fields["prazo_interno"].initial = self.instance.prazo_interno
            self.fields["participantes"].initial = list(
                self.instance.participantes.values_list("usuario_id", flat=True)
            )
            aud = extrair_audiencia(self.instance.metadados)
            self.fields["modalidade_audiencia"].initial = aud["modalidade"]
            self.fields["local_audiencia"].initial = aud["local"]
            self.fields["link_audiencia"].initial = aud["link"]
            self.fields["vara_audiencia"].initial = aud["vara"]
            self.fields["tribunal_audiencia"].initial = aud["tribunal"]
            self.fields["observacoes_audiencia"].initial = aud["observacoes"]
            follow = extrair_followup(self.instance.metadados)
            self.fields["followup_observacao"].initial = follow["observacao"]
            self.fields["followup_oportunidade"].initial = follow["oportunidade_referencia"]
            if self.instance.confirmacao_consulta:
                self.fields["confirmacao_consulta"].initial = self.instance.confirmacao_consulta
            if self.instance.origem_lead:
                self.fields["origem_lead"].initial = self.instance.origem_lead
            if self.instance.lembrete_minutos:
                self.fields["lembrete_minutos"].initial = str(
                    self.instance.lembrete_minutos
                )
        self.order_fields(
            [
                "titulo",
                "tipo",
                "status",
                "prioridade",
                "recorrencia",
                "lembrete_minutos",
                "cliente",
                "processo_referencia",
                "area_juridica",
                "confirmacao_consulta",
                "origem_lead",
                "prazo_oficial",
                "prazo_interno",
                "modalidade_audiencia",
                "tribunal_audiencia",
                "local_audiencia",
                "link_audiencia",
                "vara_audiencia",
                "observacoes_audiencia",
                "followup_observacao",
                "followup_oportunidade",
                "data",
                "hora_inicio",
                "hora_fim",
                "descricao",
                "responsavel",
                "participantes",
            ]
        )

    def clean_cliente(self):
        cliente = self.cleaned_data.get("cliente")
        if cliente and self.user and cliente.user_id != self.user.pk:
            raise forms.ValidationError("Cliente não pertence ao seu cadastro.")
        return cliente

    def clean_responsavel(self):
        responsavel = self.cleaned_data.get("responsavel")
        if responsavel and self.user and not responsavel_permitido(self.user, responsavel):
            raise forms.ValidationError("Responsável não pertence ao seu escritório.")
        return responsavel

    def clean_participantes(self):
        participantes = self.cleaned_data.get("participantes")
        if participantes and self.user and not participantes_permitidos(
            self.user, participantes
        ):
            raise forms.ValidationError("Participante inválido para esta agenda.")
        return participantes

    def clean(self):
        cleaned = super().clean()
        data = cleaned.get("data")
        hora_inicio = cleaned.get("hora_inicio")
        hora_fim = cleaned.get("hora_fim")
        if not data or not hora_inicio or not hora_fim:
            raise forms.ValidationError(
                "Preencha título, data, horário inicial e horário final."
            )

        data_hora_inicio = datetime.combine(data, hora_inicio)
        data_hora_fim = datetime.combine(data, hora_fim)
        if timezone.is_naive(data_hora_inicio):
            data_hora_inicio = timezone.make_aware(data_hora_inicio)
        if timezone.is_naive(data_hora_fim):
            data_hora_fim = timezone.make_aware(data_hora_fim)

        if data_hora_fim <= data_hora_inicio:
            data_hora_fim += timedelta(days=1)

        if data_hora_fim <= data_hora_inicio:
            raise forms.ValidationError(
                "Não foi possível validar o intervalo do compromisso."
            )

        cleaned["data_hora_inicio"] = data_hora_inicio
        cleaned["data_hora_fim"] = data_hora_fim

        tipo = cleaned.get("tipo")
        prazo_oficial = cleaned.get("prazo_oficial")
        prazo_interno = cleaned.get("prazo_interno")
        if tipo == TipoCompromisso.PRAZO:
            if prazo_interno and prazo_oficial and prazo_interno > prazo_oficial:
                self.add_error(
                    "prazo_interno",
                    "O prazo interno deve ser anterior ou igual ao prazo oficial.",
                )
            if not prazo_oficial and not prazo_interno:
                self.add_error(
                    "prazo_oficial",
                    "Informe o prazo oficial ou o prazo interno.",
                )
        else:
            cleaned["prazo_oficial"] = None
            cleaned["prazo_interno"] = None

        if tipo != TipoCompromisso.AUDIENCIA:
            cleaned["modalidade_audiencia"] = ""
            cleaned["local_audiencia"] = ""
            cleaned["link_audiencia"] = ""
            cleaned["vara_audiencia"] = ""
            cleaned["tribunal_audiencia"] = ""
            cleaned["observacoes_audiencia"] = ""

        if tipo != TipoCompromisso.FOLLOWUP_COMERCIAL:
            cleaned["followup_observacao"] = ""
            cleaned["followup_oportunidade"] = ""

        if tipo != TipoCompromisso.CONSULTA:
            cleaned["confirmacao_consulta"] = ""
            cleaned["origem_lead"] = ""
            cleaned["area_juridica"] = ""

        return cleaned

    def _post_clean(self):
        if self.user:
            self.instance.user = self.user
        super()._post_clean()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.user = self.user
        instance.data_hora = self.cleaned_data["data_hora_inicio"]
        instance.data_hora_fim = self.cleaned_data["data_hora_fim"]
        instance.prazo_oficial = self.cleaned_data.get("prazo_oficial")
        instance.prazo_interno = self.cleaned_data.get("prazo_interno")
        lembrete = self.cleaned_data.get("lembrete_minutos")
        if lembrete is None and self.instance.pk:
            lembrete = self.instance.lembrete_minutos
        instance.lembrete_minutos = lembrete
        if instance.tipo == TipoCompromisso.CONSULTA:
            conf = self.cleaned_data.get("confirmacao_consulta") or ""
            if not conf and instance.status in (
                StatusCompromisso.AGENDADO,
                StatusCompromisso.CONFIRMADO,
            ):
                conf = StatusConfirmacaoConsulta.PENDENTE
            instance.confirmacao_consulta = conf
            instance.origem_lead = self.cleaned_data.get("origem_lead") or ""
        else:
            instance.confirmacao_consulta = ""
            instance.origem_lead = ""
        base_meta = instance.metadados if instance.pk else {}
        instance.metadados = aplicar_metadados_por_tipo(
            base_meta,
            tipo=instance.tipo,
            audiencia={
                "modalidade": self.cleaned_data.get("modalidade_audiencia") or "",
                "local": self.cleaned_data.get("local_audiencia") or "",
                "link": self.cleaned_data.get("link_audiencia") or "",
                "vara": self.cleaned_data.get("vara_audiencia") or "",
                "tribunal": self.cleaned_data.get("tribunal_audiencia") or "",
                "observacoes": self.cleaned_data.get("observacoes_audiencia") or "",
            },
            followup={
                "observacao": self.cleaned_data.get("followup_observacao") or "",
                "oportunidade_referencia": self.cleaned_data.get("followup_oportunidade") or "",
            },
        )
        if not instance.responsavel_id:
            instance.responsavel = self.user
        if commit:
            instance.save()
            participantes = list(self.cleaned_data.get("participantes") or [])
            ids = {usuario.pk for usuario in participantes}
            instance.participantes.exclude(usuario_id__in=ids).delete()
            for usuario in participantes:
                CompromissoParticipante.objects.get_or_create(
                    compromisso=instance,
                    usuario=usuario,
                )
        return instance


class TarefaForm(forms.ModelForm):
    class Meta:
        model = Tarefa
        fields = [
            "titulo",
            "status",
            "prioridade",
            "cliente",
            "processo_referencia",
            "prazo",
            "descricao",
            "responsavel",
        ]
        widgets = {
            "titulo": forms.TextInput(attrs={"class": _INPUT}),
            "status": forms.Select(attrs={"class": _SELECT}),
            "prioridade": forms.Select(attrs={"class": _SELECT}),
            "cliente": forms.Select(attrs={"class": _SELECT}),
            "processo_referencia": forms.TextInput(
                attrs={
                    "class": _INPUT,
                    "placeholder": "0000000-00.0000.0.00.0000",
                    "autocomplete": "off",
                }
            ),
            "prazo": forms.DateInput(attrs={"type": "date", "class": _DATE}),
            "descricao": forms.Textarea(attrs={"class": _INPUT, "rows": 3}),
            "responsavel": forms.Select(attrs={"class": _SELECT}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.auto_id = "tarefa_%s"
        self.fields["descricao"].required = False
        self.fields["processo_referencia"].required = False
        if user:
            membros = membros_agenda(user)
            self.fields["responsavel"].queryset = membros
            if not self.instance.pk:
                self.fields["responsavel"].initial = user.pk
            self.fields["responsavel"].empty_label = None
            self.fields["cliente"].queryset = Cliente.objects.filter(user=user).order_by(
                "nome"
            )
        self.fields["cliente"].required = False
        self.fields["cliente"].empty_label = "Nenhum"
        cliente_id = None
        if self.data.get("cliente"):
            try:
                cliente_id = int(self.data.get("cliente"))
            except (TypeError, ValueError):
                cliente_id = None
        elif self.instance.pk and self.instance.cliente_id:
            cliente_id = self.instance.cliente_id
        sugestoes = processos_distintos(user, cliente_id=cliente_id) if user else []
        datalist_id = "processos-tarefa-sugeridos"
        self.fields["processo_referencia"].widget.attrs["list"] = datalist_id
        self.fields["processo_referencia"].widget.attrs["data-datalist"] = datalist_id
        self._processos_datalist_id = datalist_id
        self.processos_sugeridos = sugestoes
        self.fields["status"].choices = [
            choice
            for choice in StatusTarefa.choices
            if choice[0] != StatusTarefa.CANCELADA
        ]

    def clean_responsavel(self):
        responsavel = self.cleaned_data.get("responsavel")
        if responsavel and self.user and not responsavel_permitido(self.user, responsavel):
            raise forms.ValidationError("Responsável não pertence ao seu escritório.")
        return responsavel

    def clean_cliente(self):
        cliente = self.cleaned_data.get("cliente")
        if cliente and self.user and cliente.user_id != self.user.pk:
            raise forms.ValidationError("Cliente não pertence ao seu cadastro.")
        return cliente

    def _post_clean(self):
        if self.user:
            self.instance.user = self.user
        super()._post_clean()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.user = self.user
        if not instance.responsavel_id:
            instance.responsavel = self.user
        if commit:
            instance.save()
        return instance
