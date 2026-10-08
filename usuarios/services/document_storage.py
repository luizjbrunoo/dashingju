"""Entrega de arquivos tenant-owned.

PATH/filename/MEDIA_URL/object key não são autorização.
O caller deve autorizar Documento → Cliente → Organization antes.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import tempfile
import uuid

from django.http import FileResponse, Http404
from django.urls import reverse

logger = logging.getLogger(__name__)

TENANT_UPLOAD_PREFIX = "documentos/"
_SAFE_EXT = re.compile(r"^[a-zA-Z0-9]{1,8}$")


def _organization_id_of_instance(instance) -> int | None:
    cliente = getattr(instance, "cliente", None)
    if cliente is None:
        cliente_id = getattr(instance, "cliente_id", None)
        if cliente_id:
            from usuarios.models import Cliente

            cliente = (
                Cliente.objects.filter(pk=cliente_id)
                .only("organization_id")
                .first()
            )
    if cliente is None:
        return None
    org_id = getattr(cliente, "organization_id", None)
    if org_id is not None:
        return org_id
    org = getattr(cliente, "organization", None)
    return getattr(org, "pk", None)


def _safe_extension(filename: str) -> str:
    raw = os.path.basename(filename or "")
    ext = os.path.splitext(raw)[1].lstrip(".").lower()
    if ext and _SAFE_EXT.match(ext):
        return f".{ext}"
    return ""


def documento_upload_to(instance, filename: str) -> str:
    """Object key: documentos/org_<id>/<uuid><ext>. Sem slug, sem PII."""
    org_id = _organization_id_of_instance(instance)
    if org_id is None:
        raise ValueError("MISSING_ORGANIZATION")
    ident = uuid.uuid4().hex
    return f"{TENANT_UPLOAD_PREFIX}org_{org_id}/{ident}{_safe_extension(filename)}"


def delete_documento_blob(instance) -> None:
    """Remove o blob do backend de storage. Idempotente. Não usa path local."""
    arquivo = getattr(instance, "arquivo", None)
    name = (getattr(arquivo, "name", None) or "").strip()
    if not name:
        return
    storage = getattr(arquivo, "storage", None)
    if storage is None:
        return
    try:
        storage.delete(name)
    except Exception:
        logger.info(
            "skip model=Documentos pk=%s reason=BLOB_DELETE_FAILED",
            getattr(instance, "pk", None),
        )


def url_download_documento(documento_id: int) -> str:
    return reverse("documento_download", args=[documento_id])


def filename_para_download(arquivo) -> str:
    raw = os.path.basename(getattr(arquivo, "name", "") or "documento")
    cleaned = (
        raw.replace('"', "")
        .replace("'", "")
        .replace("\r", "")
        .replace("\n", "")
        .replace("\\", "")
        .replace("/", "")
    )
    return cleaned or "documento"


def abrir_arquivo_storage(arquivo):
    """Lê pelo backend de storage. Nunca via URL pública."""
    if not arquivo:
        raise FileNotFoundError("empty")
    return arquivo.open("rb")


def entregar_arquivo(arquivo, *, documento_id=None):
    try:
        fh = abrir_arquivo_storage(arquivo)
    except Exception:
        logger.info("documento_download missing_file pk=%s", documento_id)
        raise Http404()
    name = filename_para_download(arquivo)
    content_type, _ = mimetypes.guess_type(name)
    if not content_type:
        content_type = "application/octet-stream"
    return FileResponse(
        fh,
        as_attachment=True,
        filename=name,
        content_type=content_type,
    )


def local_path_for_backend_read(arquivo):
    """Path nativo opcional, senão tempfile a partir de FileField.open.

    .path não é requisito. Não usa .url. Retorna (path, cleanup).
    """
    if not arquivo:
        raise FileNotFoundError("empty")
    try:
        native = arquivo.path
    except (NotImplementedError, AttributeError, ValueError):
        native = None
    if native and os.path.isfile(native):
        return native, (lambda: None)

    suffix = os.path.splitext(filename_para_download(arquivo))[1] or ".bin"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        with arquivo.open("rb") as src:
            tmp.write(src.read())
        tmp.close()
    except Exception:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise

    def _cleanup():
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    return tmp.name, _cleanup
