"""Geração simples de relatório financeiro em PDF (ReportLab)."""

from decimal import Decimal
from io import BytesIO

from django.utils.formats import number_format

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _money(value) -> str:
    if value is None:
        value = Decimal("0")
    v = Decimal(str(value))
    return "R$ " + number_format(v, decimal_pos=2, grouping=True)


def gerar_relatorio_pdf(
    *,
    titulo_usuario: str,
    periodo_texto: str,
    linhas: list[dict],
    totais: dict,
    saldos_por_banco: list[dict],
) -> bytes:
    """
    linhas: lista de dicts com keys data, banco, categoria, tipo (Receita/Despesa), valor, descricao (opcional)
    totais: keys total_receitas, total_despesas, saldo_periodo
    saldos_por_banco: lista de dict banco, saldo
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title="Relatório financeiro",
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("<b>Relatório financeiro</b>", styles["Title"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(f"Usuário: {titulo_usuario}", styles["Normal"]))
    story.append(Paragraph(f"Período: {periodo_texto}", styles["Normal"]))
    story.append(Spacer(1, 0.6 * cm))

    story.append(Paragraph("<b>Saldos por banco</b>", styles["Heading2"]))
    data_sb = [["Banco", "Saldo atual"]]
    for row in saldos_por_banco:
        data_sb.append([row["banco"], _money(row["saldo"])])
    t_sb = Table(data_sb, colWidths=[12 * cm, 5 * cm])
    t_sb.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1b5c47")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
            ]
        )
    )
    story.append(t_sb)
    story.append(Spacer(1, 0.6 * cm))

    story.append(Paragraph("<b>Extrato no período</b>", styles["Heading2"]))
    data_ex = [["Data", "Banco", "Categoria", "Tipo", "Valor", "Descrição"]]
    for ln in linhas:
        data_ex.append(
            [
                ln.get("data", ""),
                ln.get("banco", "")[:28],
                ln.get("categoria", "")[:22],
                ln.get("tipo", ""),
                _money(ln.get("valor")),
                (ln.get("descricao") or "")[:40],
            ]
        )
    t_ex = Table(data_ex, repeatRows=1, colWidths=[2.2 * cm, 3 * cm, 2.8 * cm, 2 * cm, 2.2 * cm, 3.8 * cm])
    t_ex.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#134a3a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f8f8")]),
            ]
        )
    )
    story.append(t_ex)
    story.append(Spacer(1, 0.5 * cm))

    story.append(
        Paragraph(
            f"<b>Totais no período:</b> Receitas {_money(totais.get('total_receitas'))} &nbsp; "
            f"Despesas {_money(totais.get('total_despesas'))} &nbsp; "
            f"Saldo {_money(totais.get('saldo_periodo'))}",
            styles["Normal"],
        )
    )

    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf
