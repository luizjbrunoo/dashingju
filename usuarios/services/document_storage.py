"""Entrega de arquivos tenant-owned.

PATH/filename/MEDIA_URL não são autorização.
O caller deve autorizar Documento → Cliente → Organization antes.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import tempfile

from django.http import FileResponse, Http404
from django.urls import reverse

logger = logging.getLogger(__name__)

TENANT_UPLOAD_PREFIX = "documentos/"


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
    """Path nativo do storage ou temp a partir de FileField.open.

    Não usa .url. Retorna (path, cleanup).
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
