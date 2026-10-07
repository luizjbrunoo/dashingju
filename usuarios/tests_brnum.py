from decimal import Decimal

from django.template import Context, Template
from django.test import SimpleTestCase

from usuarios.br_format import format_currency_br, format_number_br


class FormatNumberBrTests(SimpleTestCase):
    def test_inteiros_com_milhar(self):
        casos = [
            (0, "0"),
            (10, "10"),
            (100, "100"),
            (999, "999"),
            (1000, "1.000"),
            (1500, "1.500"),
            (10000, "10.000"),
            (15000, "15.000"),
            (100000, "100.000"),
            (999999, "999.999"),
            (1000000, "1.000.000"),
            (1250000, "1.250.000"),
            (10000000, "10.000.000"),
        ]
        for valor, esperado in casos:
            with self.subTest(valor=valor):
                self.assertEqual(format_number_br(valor, 0), esperado)

    def test_decimais_e_moeda(self):
        self.assertEqual(format_number_br(Decimal("15000.50"), 2), "15.000,50")
        self.assertEqual(format_number_br(Decimal("1250000.75"), 2), "1.250.000,75")
        self.assertEqual(format_currency_br(Decimal("15000.50")), "R$ 15.000,50")
        self.assertEqual(format_currency_br(Decimal("1250000.75")), "R$ 1.250.000,75")
        self.assertEqual(format_currency_br(0), "R$ 0,00")

    def test_negativos(self):
        self.assertEqual(format_number_br(-15000, 0), "-15.000")
        self.assertEqual(format_number_br(Decimal("-15000.50"), 2), "-15.000,50")
        self.assertEqual(format_currency_br(Decimal("-15000.50")), "R$ -15.000,50")

    def test_nulo_permanece_vazio(self):
        self.assertEqual(format_number_br(None), "")
        self.assertEqual(format_currency_br(None), "")

    def test_filtro_de_template(self):
        html = Template("{{ n|brnum:0 }} {{ m|brnum:2 }}").render(
            Context({"n": 15000, "m": Decimal("15000.50")})
        )
        self.assertEqual(html, "15.000 15.000,50")
