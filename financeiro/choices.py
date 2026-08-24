from django.db import models


class StatusCobranca(models.TextChoices):
    DRAFT = "draft", "Rascunho"
    PENDING = "pending", "A vencer"
    DUE_SOON = "due_soon", "Vence em breve"
    OVERDUE = "overdue", "Vencido"
    PAID = "paid", "Recebido"
    PARTIALLY_PAID = "partially_paid", "Parcialmente recebido"
    CANCELED = "canceled", "Cancelado"


class CategoriaCobranca(models.TextChoices):
    HONORARIOS = "honorarios", "Honorários"
    CONSULTORIA = "consultoria", "Consultoria"
    MENSALIDADE = "mensalidade", "Mensalidade"
    EXITO = "exito", "Êxito"
    CUSTAS = "custas", "Custas reembolsáveis"
    OUTRO = "outro", "Outro"


class FormaPagamento(models.TextChoices):
    PIX = "pix", "Pix"
    BOLETO = "boleto", "Boleto"
    TRANSFERENCIA = "transferencia", "Transferência"
    CARTAO = "cartao", "Cartão"
    DINHEIRO = "dinheiro", "Dinheiro"
    OUTRO = "outro", "Outro"


class PeriodicidadeParcela(models.TextChoices):
    MENSAL = "mensal", "Mensal"
    QUINZENAL = "quinzenal", "Quinzenal"


class StatusContrato(models.TextChoices):
    DRAFT = "draft", "Rascunho"
    ACTIVE = "active", "Ativo"
    CLOSED = "closed", "Encerrado"
    CANCELED = "canceled", "Cancelado"


class AcaoCobrancaHistorico(models.TextChoices):
    CRIADA = "criada", "Cobrança criada"
    EDITADA = "editada", "Cobrança editada"
    CANCELADA = "cancelada", "Cobrança cancelada"
    VENCIMENTO_ALTERADO = "vencimento_alterado", "Vencimento alterado"
    RESPONSAVEL_ALTERADO = "responsavel_alterado", "Responsável alterado"
    RECEBIMENTO_REGISTRADO = "recebimento_registrado", "Recebimento registrado"
    RECEBIMENTO_ESTORNADO = "recebimento_estornado", "Recebimento estornado"
    PARCELAMENTO_CRIADO = "parcelamento_criado", "Parcelamento criado"
    AGENDA_VINCULADA = "agenda_vinculada", "Vinculada à agenda"
    COBRANCAS_GERADAS_CONTRATO = "cobrancas_geradas_contrato", "Cobranças geradas do contrato"
    STATUS_ALTERADO = "status_alterado", "Status alterado"
    VENCIDA = "vencida", "Cobrança vencida"
    MENSAGEM_COBRANCA = "mensagem_cobranca", "Mensagem de cobrança gerada"


class TomMensagemCobranca(models.TextChoices):
    CORDIAL = "cordial", "Cordial"
    OBJETIVO = "objetivo", "Objetivo"
    FORMAL = "formal", "Formal"
    RELACIONAMENTO = "relacionamento", "Relacionamento"


DIAS_VENCE_EM_BREVE = 7
