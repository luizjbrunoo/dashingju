import importlib
from django.shortcuts import get_object_or_404
from ia.agents import JuriAI
from usuarios.models import Documentos



def ocr_and_markdown_file(documento_id: int):
    documento = get_object_or_404(Documentos, id=documento_id)
    try:
        converter_module = importlib.import_module("docling.document_converter")
        converter = converter_module.DocumentConverter()
        result = converter.convert(documento.arquivo.path)
        texto = result.document.export_to_markdown()
    except Exception:
        # Fallback para ambiente sem docling/torch (ex.: erro de DLL no Windows)
        texto = f"Documento recebido: {documento.arquivo.name}"

    documento.content = texto
    documento.save(update_fields=["content"])
    return "ok"


def rag_documentos(documento_id: int) -> str:
    documento = get_object_or_404(Documentos, id=documento_id)
    JuriAI.knowledge.insert(
        name=documento.arquivo.name,
        text_content=documento.content or "",
        metadata={
            "cliente_id": documento.cliente.id,
            "name": documento.arquivo.name,
        },
    )
    return "ok"
