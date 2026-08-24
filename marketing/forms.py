from django import forms

from financeiro.forms import _INPUT_CLASS, _SELECT_CLASS

from .choices import (
    AREAS_JURIDICAS_PADRAO,
    FORMATOS_REAPROVEITAMENTO,
    FREQUENCIA_PLANO,
    FORMATOS_POR_CANAL,
    CanalConteudo,
    ObjetivoConteudo,
    PeriodoPlano,
    StatusConteudo,
    TAMANHO_ARTIGO,
    TomComunicacao,
)
from .models import ContentItem, ContentProfile


class ContentProfileForm(forms.ModelForm):
    class Meta:
        model = ContentProfile
        fields = [
            "nome_escritorio",
            "descricao",
            "areas_atuacao",
            "publico",
            "regiao",
            "tom_voz",
            "diferenciais",
            "palavras_preferidas",
            "palavras_evitar",
            "ctas_permitidas",
            "observacoes_institucionais",
        ]
        widgets = {
            "nome_escritorio": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "descricao": forms.Textarea(attrs={"class": _INPUT_CLASS, "rows": 3}),
            "areas_atuacao": forms.Textarea(
                attrs={
                    "class": _INPUT_CLASS,
                    "rows": 5,
                    "placeholder": "Trabalhista\nPrevidenciário\n...",
                }
            ),
            "publico": forms.Textarea(attrs={"class": _INPUT_CLASS, "rows": 2}),
            "regiao": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "tom_voz": forms.Select(attrs={"class": _SELECT_CLASS}),
            "diferenciais": forms.Textarea(attrs={"class": _INPUT_CLASS, "rows": 3}),
            "palavras_preferidas": forms.Textarea(attrs={"class": _INPUT_CLASS, "rows": 2}),
            "palavras_evitar": forms.Textarea(attrs={"class": _INPUT_CLASS, "rows": 2}),
            "ctas_permitidas": forms.Textarea(attrs={"class": _INPUT_CLASS, "rows": 2}),
            "observacoes_institucionais": forms.Textarea(attrs={"class": _INPUT_CLASS, "rows": 3}),
        }


class ContentItemForm(forms.ModelForm):
    duracao_estimada = forms.CharField(
        required=False,
        label="Duração estimada",
        widget=forms.TextInput(
            attrs={"class": _INPUT_CLASS, "placeholder": "Ex.: 8 minutos"}
        ),
    )
    tamanho_aproximado = forms.ChoiceField(
        required=False,
        label="Tamanho aproximado",
        choices=[("", "Selecione")] + list(TAMANHO_ARTIGO),
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )
    num_slides = forms.IntegerField(
        required=False,
        min_value=3,
        max_value=15,
        label="Número de slides (carrossel)",
        widget=forms.NumberInput(attrs={"class": _INPUT_CLASS, "min": 3, "max": 15}),
    )
    conteudo_origem = forms.ModelChoiceField(
        required=False,
        label="Transformar conteúdo existente (newsletter)",
        queryset=ContentItem.objects.none(),
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )

    class Meta:
        model = ContentItem
        fields = [
            "titulo",
            "tema",
            "area_juridica",
            "canal",
            "formato",
            "objetivo",
            "publico",
            "palavra_chave",
            "tom",
            "cta",
            "status",
            "data_planejada",
            "corpo",
        ]
        widgets = {
            "titulo": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "tema": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "area_juridica": forms.Select(attrs={"class": _SELECT_CLASS}),
            "canal": forms.Select(attrs={"class": _SELECT_CLASS}),
            "formato": forms.Select(attrs={"class": _SELECT_CLASS}),
            "objetivo": forms.Select(attrs={"class": _SELECT_CLASS}),
            "publico": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "palavra_chave": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "tom": forms.Select(attrs={"class": _SELECT_CLASS}),
            "cta": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "status": forms.Select(attrs={"class": _SELECT_CLASS}),
            "data_planejada": forms.DateInput(
                attrs={"class": _INPUT_CLASS, "type": "date"},
                format="%Y-%m-%d",
            ),
            "corpo": forms.Textarea(attrs={"class": _INPUT_CLASS, "rows": 10}),
        }

    def __init__(self, *args, areas_juridicas=None, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        areas = areas_juridicas or AREAS_JURIDICAS_PADRAO
        self.fields["area_juridica"].widget = forms.Select(
            attrs={"class": _SELECT_CLASS},
            choices=[("", "Selecione")] + [(a, a) for a in areas],
        )
        if usuario:
            self.fields["conteudo_origem"].queryset = ContentItem.objects.filter(
                usuario=usuario
            ).exclude(pk=self.instance.pk if self.instance.pk else None)
        canal = self.data.get("canal") if self.data.get("canal") else None
        if not canal and self.instance and self.instance.pk:
            canal = self.instance.canal
        self._configurar_formatos(canal)
        if self.instance and self.instance.pk:
            meta = self.instance.metadados or {}
            self.fields["duracao_estimada"].initial = meta.get("duracao_estimada", "")
            self.fields["tamanho_aproximado"].initial = meta.get("tamanho_aproximado", "")
            self.fields["num_slides"].initial = meta.get("num_slides", 8)

    def _configurar_formatos(self, canal):
        opcoes = [("", "Selecione")]
        if canal and canal in FORMATOS_POR_CANAL:
            opcoes += FORMATOS_POR_CANAL[canal]
        self.fields["formato"].widget = forms.Select(attrs={"class": _SELECT_CLASS}, choices=opcoes)

    def save(self, commit=True):
        instance = super().save(commit=False)
        meta = dict(instance.metadados or {})
        if self.cleaned_data.get("duracao_estimada"):
            meta["duracao_estimada"] = self.cleaned_data["duracao_estimada"]
        if self.cleaned_data.get("tamanho_aproximado"):
            meta["tamanho_aproximado"] = self.cleaned_data["tamanho_aproximado"]
        if self.cleaned_data.get("num_slides"):
            meta["num_slides"] = self.cleaned_data["num_slides"]
        instance.metadados = meta
        if commit:
            instance.save()
        return instance


class BibliotecaFiltroForm(forms.Form):
    canal = forms.ChoiceField(
        required=False,
        choices=[("", "Todos")] + list(CanalConteudo.choices),
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )
    area_juridica = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": _INPUT_CLASS, "placeholder": "Área jurídica"}),
    )
    formato = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": _INPUT_CLASS, "placeholder": "Formato"}),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", "Todos")] + list(StatusConteudo.choices),
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )
    q = forms.CharField(
        required=False,
        label="Busca",
        widget=forms.TextInput(attrs={"class": _INPUT_CLASS, "placeholder": "Título ou tema"}),
    )


class PlanoEditorialForm(forms.Form):
    area_juridica = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": _INPUT_CLASS, "placeholder": "Ex.: Trabalhista"}),
    )
    publico = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": _INPUT_CLASS, "placeholder": "Ex.: Empregados CLT"}),
    )
    objetivo = forms.ChoiceField(
        choices=[("", "Selecione")] + list(ObjetivoConteudo.choices),
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )
    periodo_dias = forms.ChoiceField(
        label="Período",
        choices=[(str(v), label) for v, label in PeriodoPlano.choices],
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )
    frequencia = forms.ChoiceField(
        label="Frequência desejada",
        choices=[("", "Selecione")] + list(FREQUENCIA_PLANO),
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )
    canais = forms.CharField(
        required=False,
        label="Canais",
        widget=forms.TextInput(
            attrs={
                "class": _INPUT_CLASS,
                "placeholder": "Instagram, Blog, LinkedIn, YouTube",
            }
        ),
    )

    def clean_periodo_dias(self):
        return int(self.cleaned_data["periodo_dias"])


class BancoIdeiasForm(forms.Form):
    area_juridica = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": _INPUT_CLASS}),
    )
    publico = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": _INPUT_CLASS}),
    )
    objetivo = forms.ChoiceField(
        choices=[("", "Selecione")] + list(ObjetivoConteudo.choices),
        required=False,
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )

    def __init__(self, *args, areas_juridicas=None, **kwargs):
        super().__init__(*args, **kwargs)
        if areas_juridicas:
            self.fields["area_juridica"].widget = forms.Select(
                attrs={"class": _SELECT_CLASS},
                choices=[("", "Selecione")] + [(a, a) for a in areas_juridicas],
            )


class IdeiaAgendarForm(forms.Form):
    data_planejada = forms.DateField(
        widget=forms.DateInput(attrs={"class": _INPUT_CLASS, "type": "date"}),
    )


class ReaproveitarForm(forms.Form):
    formatos = forms.MultipleChoiceField(
        label="Formatos de destino",
        choices=FORMATOS_REAPROVEITAMENTO,
        widget=forms.CheckboxSelectMultiple(
            attrs={"class": "rounded border-zinc-700 bg-zinc-900 text-bally-bright"}
        ),
        error_messages={"required": "Selecione ao menos um formato."},
    )

    def clean_formatos(self):
        formatos = self.cleaned_data.get("formatos") or []
        if not formatos:
            raise forms.ValidationError("Selecione ao menos um formato.")
        return formatos
