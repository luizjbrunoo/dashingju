import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django_q.tasks import Chain
from .models import Documentos

logger = logging.getLogger(__name__)

try:
    from ia.tasks import ocr_and_markdown_file, rag_documentos
except ModuleNotFoundError:
    ocr_and_markdown_file = None
    rag_documentos = None


@receiver(post_save, sender=Documentos)
def post_save_documentos(sender, instance, created, **kwargs):
    
    if not (created and ocr_and_markdown_file and rag_documentos):
        return
    try:
        chain = Chain()
        chain.append(ocr_and_markdown_file, instance.id)
        chain.append(rag_documentos, instance.id)
        chain.run()
    except Exception:
        # Não quebra o POST se a fila (Redis/ORM) falhar; loga para diagnóstico
        logger.exception(
            "Falha ao enfileirar chain Django-Q para Documentos id=%s",
            getattr(instance, "id", None),
        )