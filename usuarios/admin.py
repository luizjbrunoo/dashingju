from django.contrib import admin

from .models import (
    AgendaAuditLog,
    Cliente,
    Compromisso,
    CompromissoParticipante,
    Documentos,
    Tarefa,
)

admin.site.register(Cliente)
admin.site.register(Documentos)
admin.site.register(Compromisso)
admin.site.register(Tarefa)
admin.site.register(CompromissoParticipante)
admin.site.register(AgendaAuditLog)
