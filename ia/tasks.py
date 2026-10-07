"""Tarefas assíncronas de Documentos (OCR + RAG).

PK global é aceitável. Side effect só após validar
Documento → Cliente → Organization.
"""

from __future__ import annotations

import importlib
import logging

from ia.services.docs_tenancy import (
    REASON_MISSING_ORGANIZATION,
    REASON_NOT_FOUND,
    documento_tenant_safe,
    load_documento,
    log_skip_documento,
    organization_of_documento,
)
from ia.services.document_knowledge import index_document

logger = logging.getLogger(__name__)


def ocr_and_markdown_file(documento_id: int):
    documento = load_documento(documento_id)
    if documento is None:
        log_skip_documento(documento_id, REASON_NOT_FOUND)
        return "skip"
    if not documento_tenant_safe(documento):
        log_skip_documento(documento_id, REASON_MISSING_ORGANIZATION)
        return "skip"

    cleanup = None
    try:
        converter_module = importlib.import_module("docling.document_converter")
        converter = converter_module.DocumentConverter()
        from usuarios.services.document_storage import local_path_for_backend_read

        local_path, cleanup = local_path_for_backend_read(documento.arquivo)
        result = converter.convert(local_path)
        texto = result.document.export_to_markdown()
    except Exception:
        nome = getattr(getattr(documento, "arquivo", None), "name", "") or ""
        texto = f"Documento recebido: {nome}"
    finally:
        if cleanup:
            cleanup()

    documento.content = texto
    documento.save(update_fields=["content"])
    return "ok"


def rag_documentos(documento_id: int) -> str:
    documento = load_documento(documento_id)
    if documento is None:
        log_skip_documento(documento_id, REASON_NOT_FOUND)
        return "skip"
    organization = organization_of_documento(documento)
    if organization is None:
        log_skip_documento(documento_id, REASON_MISSING_ORGANIZATION)
        return "skip"
    return index_document(organization, documento)
