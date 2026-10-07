from decimal import Decimal

from django import forms
from django.utils import timezone

from comercial.models import InvestimentoMidia, MetaComercial
from comercial.services.goals import ticket_medio_contratos, vigencia_padrao_ano

_INPUT = (
    "w-full rounded-md border border-zinc-700 bg-zinc-950/50 px-3 py-2 text-sm "
    "text-zinc-100 focus:border-bally/50 focus:ring-2 focus:ring-bally/30 outline-none"
)
_DATE = f"{_INPUT} [color-scheme:dark]"


class MetaComercialForm(forms.ModelForm):
    usar_ticket_automatico = forms.BooleanField(
        required=False,
        initial=True,
        label="Usar ticket médio automático (contratos)",
    )

    class Meta:
        model = MetaComercial
        fields = (
            "ano",
            "meta_anual",
            "meta_mensal",
            "ticket_medio",
            "vigencia_inicio",
            "vigencia_fim",
            "observacoes",
        )
        widgets = {
            "ano": forms.NumberInput(attrs={"class": _INPUT, "min": 2020, "max": 2100}),
            "meta_anual": forms.NumberInput(
                attrs={"class": _INPUT, "step": "0.01", "min": "0.01"}
            ),
            "meta_mensal": forms.NumberInput(
                attrs={"class": _INPUT, "step": "0.01", "min": "0.01"}
            ),
            "ticket_medio": forms.NumberInput(
                attrs={"class": _INPUT, "step": "0.01", "min": "0.01"}
            ),
            "vigencia_inicio": forms.DateInput(attrs={"type": "date", "class": _DATE}),
            "vigencia_fim": forms.DateInput(attrs={"type": "date", "class": _DATE}),
            "observacoes": forms.Textarea(attrs={"class": _INPUT, "rows": 3}),
        }

    def __init__(self, *args, user=None, organization=None, **kwargs):
        self.user = user
        self.organization = organization
        super().__init__(*args, **kwargs)
        hoje = timezone.localdate()
        if not self.instance.pk:
            self.fields["ano"].initial = hoje.year
            ini, fim = vigencia_padrao_ano(hoje.year)
            self.fields["vigencia_inicio"].initial = ini
            self.fields["vigencia_fim"].initial = fim
            auto = ticket_medio_contratos(organization)
            if auto and auto.valor:
                self.fields["ticket_medio"].initial = auto.valor
                self.fields["ticket_medio"].help_text = auto.mensagem
            self.fields["usar_ticket_automatico"].initial = True
        else:
            self.fields["usar_ticket_automatico"].initial = not self.instance.ticket_medio_manual

    def clean(self):
        cleaned = super().clean()
        meta_anual = cleaned.get("meta_anual")
        meta_mensal = cleaned.get("meta_mensal")
        if meta_anual and not meta_mensal:
            cleaned["meta_mensal"] = (Decimal(meta_anual) / Decimal("12")).quantize(
                Decimal("0.01")
            )
        usar_auto = cleaned.get("usar_ticket_automatico")
        if usar_auto:
            cleaned["ticket_medio"] = None
        return cleaned


class InvestimentoMidiaForm(forms.ModelForm):
    class Meta:
        model = InvestimentoMidia
        fields = ("data_inicio", "data_fim", "valor", "canal", "observacoes")
        widgets = {
            "data_inicio": forms.DateInput(attrs={"type": "date", "class": _DATE}),
            "data_fim": forms.DateInput(attrs={"type": "date", "class": _DATE}),
            "valor": forms.NumberInput(
                attrs={"class": _INPUT, "step": "0.01", "min": "0.01"}
            ),
            "canal": forms.TextInput(
                attrs={
                    "class": _INPUT,
                    "placeholder": "google_ads, instagram, geral…",
                }
            ),
            "observacoes": forms.Textarea(attrs={"class": _INPUT, "rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hoje = timezone.localdate()
        if not self.instance.pk:
            self.fields["data_inicio"].initial = hoje.replace(day=1)
            self.fields["data_fim"].initial = hoje
