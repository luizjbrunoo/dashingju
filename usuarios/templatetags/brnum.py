from django import template

from usuarios.br_format import format_currency_br, format_number_br

register = template.Library()


@register.filter(is_safe=True)
def brnum(value, decimal_pos=None):
    """Número no padrão brasileiro, com milhar. Uso: {{ x|brnum }} ou {{ x|brnum:2 }}."""
    return format_number_br(value, decimal_pos=decimal_pos)


@register.filter(is_safe=True)
def brmoney(value, decimal_pos=2):
    """Moeda no padrão brasileiro. Uso: {{ x|brmoney }} → R$ 15.000,50."""
    return format_currency_br(value, decimal_pos=decimal_pos)
