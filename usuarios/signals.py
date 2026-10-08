import logging

from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver
from django_q.tasks import Chain
from .models import Documentos

logger = logging.getLogger(__name__)

try:
    from ia.tasks import ocr_and_markdown_file, rag_documentos
except ModuleNotFoundError:
    ocr_and_markdown_file = None
    rag_documentos = None


def _documento_tenant_safe(instance) -> bool:
    try:
        from ia.services.docs_tenancy import documento_tenant_safe

        return documento_tenant_safe(instance)
    except Exception:
        return False


@receiver(post_save, sender=Documentos)
def post_save_documentos(sender, instance, created, **kwargs):
    if not (created and ocr_and_markdown_file and rag_documentos):
        return
    if not _documento_tenant_safe(instance):
        logger.info(
            "skip model=Documentos pk=%s reason=MISSING_ORGANIZATION",
            getattr(instance, "id", None),
        )
        return
    try:
        chain = Chain()
        chain.append(ocr_and_markdown_file, instance.id)
        chain.append(rag_documentos, instance.id)
        chain.run()
    except Exception:
        logger.exception(
            "Falha ao enfileirar chain Django-Q para Documentos id=%s",
            getattr(instance, "id", None),
        )


@receiver(pre_delete, sender=Documentos)
def pre_delete_documentos(sender, instance, **kwargs):
    try:
        from ia.services.docs_tenancy import organization_of_documento
        from ia.services.document_knowledge import purge_documento_vectors

        organization = organization_of_documento(instance)
        if organization is None:
            return
        purge_documento_vectors(organization, instance.pk)
    except Exception:
        logger.info(
            "skip model=Documentos pk=%s reason=VECTOR_PURGE_FAILED",
            getattr(instance, "pk", None),
        )


@receiver(post_delete, sender=Documentos)
def post_delete_documentos_blob(sender, instance, **kwargs):
    from usuarios.services.document_storage import delete_documento_blob

    delete_documento_blob(instance)
