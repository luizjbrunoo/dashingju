from django import forms
from django.core.exceptions import ValidationError

from .models import Banco, Categoria, Movimento

_SELECT_CLASS = "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"


class BancoForm(forms.ModelForm):
    class Meta:
        model = Banco
        fields = ["nome", "agencia", "conta", "saldo_inicial"]
        widgets = {
            "nome": forms.TextInput(
                attrs={"class": "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"}
            ),
            "agencia": forms.TextInput(
                attrs={"class": "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"}
            ),
            "conta": forms.TextInput(
                attrs={"class": "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"}
            ),
            "saldo_inicial": forms.NumberInput(
                attrs={
                    "class": "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100",
                    "step": "0.01",
                }
            ),
        }


class CategoriaForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["nome", "tipo"]
        widgets = {
            "nome": forms.TextInput(
                attrs={"class": "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"}
            ),
            "tipo": forms.Select(
                attrs={"class": "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"}
            ),
        }


class MovimentoForm(forms.ModelForm):
    tipo = forms.ChoiceField(
        label="Tipo",
        choices=Categoria.Tipo.choices,
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        if usuario is not None:
            self.fields["banco"].queryset = Banco.objects.filter(usuario=usuario)

            tipo_efetivo = None
            if self.data:
                tipo_efetivo = self.data.get("tipo")
            elif getattr(self.instance, "pk", None) and self.instance.categoria_id:
                tipo_efetivo = self.instance.categoria.tipo
            else:
                tipo_efetivo = Categoria.Tipo.RECEITA

            if tipo_efetivo in (Categoria.Tipo.RECEITA, Categoria.Tipo.DESPESA):
                self.fields["categoria"].queryset = Categoria.objects.filter(usuario=usuario, tipo=tipo_efetivo)
            else:
                self.fields["categoria"].queryset = Categoria.objects.none()

            if not self.data and not getattr(self.instance, "pk", None):
                self.fields["tipo"].initial = Categoria.Tipo.RECEITA
            elif getattr(self.instance, "pk", None) and self.instance.categoria_id:
                self.fields["tipo"].initial = self.instance.categoria.tipo

    class Meta:
        model = Movimento
        fields = ["banco", "categoria", "valor", "data", "descricao"]
        widgets = {
            "banco": forms.Select(
                attrs={"class": _SELECT_CLASS}
            ),
            "categoria": forms.Select(
                attrs={"class": _SELECT_CLASS}
            ),
            "valor": forms.NumberInput(
                attrs={
                    "class": "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100",
                    "step": "0.01",
                    "min": "0.01",
                }
            ),
            "data": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100",
                }
            ),
            "descricao": forms.Textarea(
                attrs={
                    "rows": 3,
                    "class": "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100",
                }
            ),
        }

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get("tipo")
        categoria = cleaned.get("categoria")
        if tipo and categoria and categoria.tipo != tipo:
            raise ValidationError("Escolha uma categoria compatível com o tipo selecionado.")
        return cleaned
