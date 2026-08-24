from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .choices import (
    CategoriaCobranca,
    FormaPagamento,
    PeriodicidadeParcela,
    StatusCobranca,
)
from .services.cobranca_parcelamento import MAX_PARCELAS, calcular_valores_parcelas
from .models import Banco, Categoria, Cobranca, Contrato, Movimento

User = get_user_model()

_SELECT_CLASS = "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
_INPUT_CLASS = "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"


def parse_decimal_br(value) -> Decimal:
    """Aceita vazio (0), ponto ou formato brasileiro (1.234,56)."""
    if value is None:
        return Decimal("0")
    s = str(value).strip()
    if not s:
        return Decimal("0")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation as exc:
        raise ValidationError("Informe um valor numérico válido (ex.: 100,50 ou 0).") from exc


class BancoForm(forms.ModelForm):
    saldo_inicial = forms.CharField(
        required=False,
        label="Saldo inicial",
        help_text="Opcional. Use vírgula para centavos (ex.: 1.500,00). Deixe em branco para R$ 0,00.",
        widget=forms.TextInput(
            attrs={
                "class": _INPUT_CLASS,
                "inputmode": "decimal",
                "placeholder": "0,00",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = Banco
        fields = ["nome", "agencia", "conta", "saldo_inicial"]
        widgets = {
            "nome": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "agencia": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "conta": forms.TextInput(attrs={"class": _INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.saldo_inicial is not None:
            self.initial["saldo_inicial"] = str(self.instance.saldo_inicial).replace(".", ",")

    def clean_saldo_inicial(self):
        return parse_decimal_br(self.cleaned_data.get("saldo_inicial"))


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
        self._usuario = usuario
        super().__init__(*args, **kwargs)
        if usuario is not None:
            self.instance.usuario = usuario
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
        if self._usuario is not None:
            self.instance.usuario = self._usuario
        cleaned = super().clean()
        tipo = cleaned.get("tipo")
        categoria = cleaned.get("categoria")
        banco = cleaned.get("banco")
        if self._usuario is not None:
            if banco and banco.usuario_id != self._usuario.pk:
                raise ValidationError({"banco": "Selecione um banco da sua conta."})
            if categoria and categoria.usuario_id != self._usuario.pk:
                raise ValidationError({"categoria": "Selecione uma categoria da sua conta."})
        if tipo and categoria and categoria.tipo != tipo:
            raise ValidationError("Escolha uma categoria compatível com o tipo selecionado.")
        return cleaned


class CobrancaForm(forms.ModelForm):
    valor = forms.CharField(
        label="Valor",
        widget=forms.TextInput(
            attrs={
                "class": _INPUT_CLASS,
                "inputmode": "decimal",
                "placeholder": "0,00",
                "autocomplete": "off",
            }
        ),
    )
    salvar_como_rascunho = forms.BooleanField(
        required=False,
        label="Salvar como rascunho",
    )
    tipo_lancamento = forms.ChoiceField(
        label="Tipo de cobrança",
        choices=[
            ("unica", "Cobrança única"),
            ("parcelada", "Parcelado"),
        ],
        initial="unica",
        required=False,
        widget=forms.RadioSelect(attrs={"class": "space-y-1 text-sm text-zinc-300"}),
    )
    num_parcelas = forms.IntegerField(
        required=False,
        min_value=2,
        max_value=MAX_PARCELAS,
        label="Número de parcelas",
        widget=forms.NumberInput(
            attrs={
                "class": _INPUT_CLASS,
                "min": "2",
                "max": str(MAX_PARCELAS),
            }
        ),
    )
    periodicidade = forms.ChoiceField(
        required=False,
        label="Periodicidade",
        choices=PeriodicidadeParcela.choices,
        initial=PeriodicidadeParcela.MENSAL,
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )

    class Meta:
        model = Cobranca
        fields = [
            "cliente",
            "contrato",
            "descricao",
            "data_vencimento",
            "categoria",
            "responsavel",
            "forma_prevista_pagamento",
            "observacoes_internas",
        ]
        widgets = {
            "cliente": forms.Select(attrs={"class": _SELECT_CLASS}),
            "contrato": forms.Select(attrs={"class": _SELECT_CLASS}),
            "descricao": forms.TextInput(
                attrs={
                    "class": _INPUT_CLASS,
                    "placeholder": "Honorários advocatícios — ...",
                }
            ),
            "data_vencimento": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": f"{_INPUT_CLASS} [color-scheme:dark]",
                }
            ),
            "categoria": forms.Select(attrs={"class": _SELECT_CLASS}),
            "responsavel": forms.Select(attrs={"class": _SELECT_CLASS}),
            "forma_prevista_pagamento": forms.Select(attrs={"class": _SELECT_CLASS}),
            "observacoes_internas": forms.Textarea(
                attrs={"class": _INPUT_CLASS, "rows": 3}
            ),
        }

    def __init__(self, *args, usuario=None, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)
        self.fields["contrato"].required = False
        self.fields["forma_prevista_pagamento"].required = False
        self.fields["observacoes_internas"].required = False
        self.fields["contrato"].empty_label = "Sem contrato vinculado"
        self.fields["forma_prevista_pagamento"].choices = [
            ("", "Não informada")
        ] + list(FormaPagamento.choices)
        if usuario:
            from usuarios.models import Cliente

            self.fields["cliente"].queryset = Cliente.objects.filter(user=usuario).order_by(
                "nome"
            )
            self.fields["contrato"].queryset = Contrato.objects.filter(
                usuario=usuario
            ).select_related("cliente").order_by("-criado_em")
            self.fields["responsavel"].queryset = User.objects.filter(pk=usuario.pk)
            self.fields["responsavel"].initial = usuario.pk
            self.fields["responsavel"].empty_label = None
        if self.instance.pk and self.instance.valor_original is not None:
            self.initial["valor"] = str(self.instance.valor_original).replace(".", ",")
        if not self.instance.pk:
            self.fields["valor"].help_text = "No parcelamento, informe o valor total."
            self.fields["data_vencimento"].help_text = (
                "No parcelamento, data do primeiro vencimento."
            )
        if self.instance.pk:
            self.fields.pop("tipo_lancamento", None)
            self.fields.pop("num_parcelas", None)
            self.fields.pop("periodicidade", None)

    def clean_tipo_lancamento(self):
        return self.cleaned_data.get("tipo_lancamento") or "unica"

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk:
            return cleaned
        if cleaned.get("tipo_lancamento") == "parcelada":
            num = cleaned.get("num_parcelas")
            if not num or num < 2:
                self.add_error("num_parcelas", "Informe pelo menos 2 parcelas.")
            elif num > MAX_PARCELAS:
                self.add_error("num_parcelas", f"Máximo de {MAX_PARCELAS} parcelas.")
            if not cleaned.get("periodicidade"):
                self.add_error("periodicidade", "Informe a periodicidade.")
            valor = cleaned.get("valor")
            if valor and num and num >= 2:
                try:
                    calcular_valores_parcelas(valor, num)
                except ValidationError as exc:
                    msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                    self.add_error(None, msg)
        return cleaned

    def clean_valor(self):
        valor = parse_decimal_br(self.cleaned_data.get("valor"))
        if valor <= 0:
            raise ValidationError("O valor deve ser maior que zero.")
        return valor

    def clean_cliente(self):
        cliente = self.cleaned_data.get("cliente")
        if cliente and self.usuario and cliente.user_id != self.usuario.pk:
            raise ValidationError("Cliente não pertence ao seu cadastro.")
        return cliente

    def clean_contrato(self):
        contrato = self.cleaned_data.get("contrato")
        cliente = self.cleaned_data.get("cliente")
        if contrato and self.usuario and contrato.usuario_id != self.usuario.pk:
            raise ValidationError("Contrato não pertence ao seu cadastro.")
        if contrato and cliente and contrato.cliente_id != cliente.pk:
            raise ValidationError("Contrato não pertence ao cliente selecionado.")
        return contrato

    def _post_clean(self):
        if self.usuario:
            self.instance.usuario = self.usuario
        super()._post_clean()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.valor_original = self.cleaned_data["valor"]
        contrato = self.cleaned_data.get("contrato")
        if contrato:
            instance.contrato = contrato
            instance.contrato_referencia = contrato.referencia
        elif not instance.contrato_id:
            instance.contrato_referencia = ""
        if self.usuario:
            instance.usuario = self.usuario
            if not instance.responsavel_id:
                instance.responsavel = self.usuario
        if self.cleaned_data.get("salvar_como_rascunho"):
            instance.status = StatusCobranca.DRAFT
        elif not instance.pk or instance.status == StatusCobranca.DRAFT:
            instance.status = StatusCobranca.PENDING
        if commit:
            instance.save()
        return instance


class RecebimentoForm(forms.Form):
    valor = forms.CharField(
        label="Valor recebido",
        widget=forms.TextInput(
            attrs={
                "class": _INPUT_CLASS,
                "inputmode": "decimal",
                "placeholder": "0,00",
                "autocomplete": "off",
            }
        ),
    )
    data_recebimento = forms.DateField(
        label="Data do recebimento",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": f"{_INPUT_CLASS} [color-scheme:dark]",
            }
        ),
    )
    forma_pagamento = forms.ChoiceField(
        label="Forma de pagamento",
        choices=FormaPagamento.choices,
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )
    referencia = forms.CharField(
        required=False,
        label="Referência",
        max_length=120,
        widget=forms.TextInput(
            attrs={
                "class": _INPUT_CLASS,
                "placeholder": "Comprovante, NSU, etc.",
                "autocomplete": "off",
            }
        ),
    )
    observacao = forms.CharField(
        required=False,
        label="Observação",
        widget=forms.Textarea(attrs={"class": _INPUT_CLASS, "rows": 3}),
    )

    def __init__(self, *args, cobranca=None, **kwargs):
        from django.utils import timezone

        self.cobranca = cobranca
        super().__init__(*args, **kwargs)
        if cobranca and cobranca.saldo > 0 and not self.initial.get("valor"):
            self.fields["valor"].initial = str(cobranca.saldo).replace(".", ",")
        if not self.initial.get("data_recebimento"):
            self.fields["data_recebimento"].initial = timezone.localdate()

    def clean_valor(self):
        valor = parse_decimal_br(self.cleaned_data.get("valor"))
        if self.cobranca:
            from financeiro.services.cobranca_recebimento import validar_valor_recebimento

            validar_valor_recebimento(self.cobranca, valor)
        elif valor <= 0:
            raise ValidationError("O valor recebido deve ser maior que zero.")
        return valor


class ContratoForm(forms.ModelForm):
    valor_total = forms.CharField(
        label="Valor total",
        widget=forms.TextInput(
            attrs={
                "class": _INPUT_CLASS,
                "inputmode": "decimal",
                "placeholder": "0,00",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = Contrato
        fields = [
            "cliente",
            "referencia",
            "descricao",
            "status",
            "responsavel",
            "observacoes",
        ]
        widgets = {
            "cliente": forms.Select(attrs={"class": _SELECT_CLASS}),
            "referencia": forms.TextInput(
                attrs={
                    "class": _INPUT_CLASS,
                    "placeholder": "Ex.: 00042",
                    "autocomplete": "off",
                }
            ),
            "descricao": forms.TextInput(
                attrs={"class": _INPUT_CLASS, "placeholder": "Honorários advocatícios"}
            ),
            "status": forms.Select(attrs={"class": _SELECT_CLASS}),
            "responsavel": forms.Select(attrs={"class": _SELECT_CLASS}),
            "observacoes": forms.Textarea(attrs={"class": _INPUT_CLASS, "rows": 3}),
        }

    def __init__(self, *args, usuario=None, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)
        self.fields["observacoes"].required = False
        if usuario:
            from usuarios.models import Cliente

            self.fields["cliente"].queryset = Cliente.objects.filter(user=usuario).order_by(
                "nome"
            )
            self.fields["responsavel"].queryset = User.objects.filter(pk=usuario.pk)
            self.fields["responsavel"].initial = usuario.pk
            self.fields["responsavel"].empty_label = None
        if self.instance.pk and self.instance.valor_total is not None:
            self.initial["valor_total"] = str(self.instance.valor_total).replace(".", ",")

    def clean_valor_total(self):
        valor = parse_decimal_br(self.cleaned_data.get("valor_total"))
        if valor <= 0:
            raise ValidationError("O valor deve ser maior que zero.")
        return valor

    def clean_cliente(self):
        cliente = self.cleaned_data.get("cliente")
        if cliente and self.usuario and cliente.user_id != self.usuario.pk:
            raise ValidationError("Cliente não pertence ao seu cadastro.")
        return cliente

    def _post_clean(self):
        if self.usuario:
            self.instance.usuario = self.usuario
        super()._post_clean()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.valor_total = self.cleaned_data["valor_total"]
        if self.usuario:
            instance.usuario = self.usuario
            if not instance.responsavel_id:
                instance.responsavel = self.usuario
        if commit:
            instance.save()
        return instance


class GerarCobrancasContratoForm(forms.Form):
    tipo_lancamento = forms.ChoiceField(
        label="Tipo de geração",
        choices=[
            ("unica", "Cobrança única"),
            ("parcelada", "Parcelado"),
        ],
        initial="unica",
        widget=forms.RadioSelect(attrs={"class": "space-y-1 text-sm text-zinc-300"}),
    )
    num_parcelas = forms.IntegerField(
        required=False,
        min_value=2,
        max_value=MAX_PARCELAS,
        label="Número de parcelas",
        widget=forms.NumberInput(
            attrs={"class": _INPUT_CLASS, "min": "2", "max": str(MAX_PARCELAS)}
        ),
    )
    periodicidade = forms.ChoiceField(
        required=False,
        label="Periodicidade",
        choices=PeriodicidadeParcela.choices,
        initial=PeriodicidadeParcela.MENSAL,
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )
    primeiro_vencimento = forms.DateField(
        label="Primeiro vencimento",
        widget=forms.DateInput(
            attrs={"type": "date", "class": f"{_INPUT_CLASS} [color-scheme:dark]"}
        ),
    )
    descricao = forms.CharField(
        required=False,
        label="Descrição das cobranças",
        widget=forms.TextInput(attrs={"class": _INPUT_CLASS}),
    )
    categoria = forms.ChoiceField(
        label="Categoria",
        choices=CategoriaCobranca.choices,
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )
    forma_prevista_pagamento = forms.ChoiceField(
        required=False,
        label="Forma prevista",
        choices=[("", "Não informada")] + list(FormaPagamento.choices),
        widget=forms.Select(attrs={"class": _SELECT_CLASS}),
    )
    confirmar = forms.BooleanField(
        label="Confirmo a geração das cobranças a partir deste contrato",
    )

    def __init__(self, *args, contrato=None, **kwargs):
        self.contrato = contrato
        super().__init__(*args, **kwargs)
        if contrato:
            self.fields["descricao"].initial = contrato.descricao

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("confirmar"):
            self.add_error("confirmar", "Confirme a geração das cobranças.")
        if cleaned.get("tipo_lancamento") == "parcelada":
            num = cleaned.get("num_parcelas")
            if not num or num < 2:
                self.add_error("num_parcelas", "Informe pelo menos 2 parcelas.")
            elif self.contrato and num:
                try:
                    calcular_valores_parcelas(self.contrato.valor_total, num)
                except ValidationError as exc:
                    msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                    self.add_error(None, msg)
        return cleaned
