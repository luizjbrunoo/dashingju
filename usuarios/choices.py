from django.db import models


class TipoCompromisso(models.TextChoices):
    CONSULTA = "consulta", "Consulta"
    REUNIAO = "reuniao", "Reunião"
    AUDIENCIA = "audiencia", "Audiência"
    PRAZO = "prazo", "Prazo"
    DILIGENCIA = "diligencia", "Diligência"
    RETORNO_CLIENTE = "retorno_cliente", "Retorno ao cliente"
    FOLLOWUP_COMERCIAL = "followup_comercial", "Follow-up comercial"
    COBRANCA = "cobranca", "Cobrança"
    INTERNO = "interno", "Interno"
    OUTRO = "outro", "Outro"


class StatusCompromisso(models.TextChoices):
    AGENDADO = "agendado", "Agendado"
    CONFIRMADO = "confirmado", "Confirmado"
    REALIZADO = "realizado", "Realizado"
    NAO_COMPARECEU = "nao_compareceu", "Não compareceu"
    CANCELADO = "cancelado", "Cancelado"


class StatusTarefa(models.TextChoices):
    PENDENTE = "pendente", "Pendente"
    EM_ANDAMENTO = "em_andamento", "Em andamento"
    CONCLUIDA = "concluida", "Concluída"
    CANCELADA = "cancelada", "Cancelada"


class Prioridade(models.TextChoices):
    BAIXA = "baixa", "Baixa"
    NORMAL = "normal", "Normal"
    ALTA = "alta", "Alta"
    URGENTE = "urgente", "Urgente"


class Recorrencia(models.TextChoices):
    NAO_REPETIR = "nao_repetir", "Não repetir"
    SEMANAL = "semanal", "Semanal"
    QUINZENAL = "quinzenal", "Quinzenal"
    MENSAL = "mensal", "Mensal"


class LembreteMinutos(models.IntegerChoices):
    MIN_15 = 15, "15 minutos antes"
    HORA_1 = 60, "1 hora antes"
    DIA_1 = 1440, "1 dia antes"
    DIAS_3 = 4320, "3 dias antes"


class ModalidadeAudiencia(models.TextChoices):
    PRESENCIAL = "presencial", "Presencial"
    ONLINE = "online", "Online"
    HIBRIDA = "hibrida", "Híbrida"


class StatusConfirmacaoConsulta(models.TextChoices):
    PENDENTE = "pendente", "Pendente"
    CONFIRMADA = "confirmada", "Confirmada"
    CANCELADA = "cancelada", "Cancelada"


class OrigemLeadConsulta(models.TextChoices):
    INDICACAO = "indicacao", "Indicação"
    SITE = "site", "Site"
    REDES_SOCIAIS = "redes_sociais", "Redes sociais"
    TELEFONE = "telefone", "Telefone"
    GOOGLE_ADS = "google_ads", "Google Ads"
    OUTRO = "outro", "Outro"


class OrigemLead(models.TextChoices):
    """Origem comercial do cliente/lead (cadastro CRM)."""

    NAO_IDENTIFICADA = "", "Origem não identificada"
    GOOGLE_ADS = "google_ads", "Google Ads"
    GOOGLE_ORGANIC = "google_organic", "Google orgânico"
    INSTAGRAM = "instagram", "Instagram"
    FACEBOOK = "facebook", "Facebook"
    WHATSAPP = "whatsapp", "WhatsApp"
    INDICACAO = "indicacao", "Indicação"
    SITE = "site", "Site"
    BLOG = "blog", "Blog"
    OUTRO = "outro", "Outro"


class EscopoAgenda(models.TextChoices):
    MINHA = "minha", "Minha agenda"
    EQUIPE = "equipe", "Equipe"
    TODOS = "todos", "Todos"
