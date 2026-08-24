from django.db import models


class ObjetivoConteudo(models.TextChoices):
    ATRAIR_CLIENTES = "atrair_clientes", "Atrair potenciais clientes"
    AUTORIDADE = "autoridade", "Construir autoridade"
    EDUCAR = "educar", "Educar audiência"
    RELACIONAMENTO = "relacionamento", "Relacionamento"
    TRAFEGO = "trafego", "Tráfego para site/blog"
    MARCA = "marca", "Fortalecer marca"


class CanalConteudo(models.TextChoices):
    INSTAGRAM = "instagram", "Instagram"
    LINKEDIN = "linkedin", "LinkedIn"
    FACEBOOK = "facebook", "Facebook"
    YOUTUBE = "youtube", "YouTube"
    BLOG = "blog", "Blog"
    EMAIL = "email", "E-mail"


class TomComunicacao(models.TextChoices):
    EDUCATIVO = "educativo", "Educativo"
    PROFISSIONAL = "profissional", "Profissional"
    DIDATICO = "didatico", "Didático"
    INSTITUCIONAL = "institucional", "Institucional"
    AUTORIDADE = "autoridade", "Autoridade"


class StatusConteudo(models.TextChoices):
    IDEIA = "ideia", "Ideia"
    RASCUNHO = "rascunho", "Rascunho"
    GERADO_IA = "gerado_ia", "Gerado por IA"
    EM_REVISAO = "em_revisao", "Em revisão"
    APROVADO = "aprovado", "Aprovado"
    AGENDADO = "agendado", "Agendado"
    PUBLICADO = "publicado", "Publicado"


class StatusIdeia(models.TextChoices):
    PENDENTE = "pendente", "Pendente"
    USADA = "usada", "Usada"
    DESCARTADA = "descartada", "Descartada"


FORMATOS_POR_CANAL: dict[str, list[tuple[str, str]]] = {
    CanalConteudo.INSTAGRAM: [
        ("post", "Post"),
        ("carrossel", "Carrossel"),
        ("reels", "Reels"),
        ("story", "Story"),
        ("legenda", "Legenda"),
    ],
    CanalConteudo.LINKEDIN: [
        ("post", "Post"),
        ("artigo", "Artigo"),
    ],
    CanalConteudo.FACEBOOK: [
        ("post", "Post"),
    ],
    CanalConteudo.YOUTUBE: [
        ("video_longo", "Vídeo longo"),
        ("short", "Short"),
        ("ideia_video", "Ideia de vídeo"),
    ],
    CanalConteudo.BLOG: [
        ("artigo", "Artigo"),
        ("guia", "Guia"),
        ("faq", "FAQ"),
    ],
    CanalConteudo.EMAIL: [
        ("newsletter", "Newsletter"),
        ("email_educativo", "E-mail educativo"),
    ],
}


class PeriodoPlano(models.IntegerChoices):
    SEMANA = 7, "7 dias"
    QUINZE = 15, "15 dias"
    MES = 30, "30 dias"
    TRIMESTRE = 90, "90 dias"


TAMANHO_ARTIGO = [
    ("curto", "Curto (~500 palavras)"),
    ("medio", "Médio (~1.000 palavras)"),
    ("longo", "Longo (~2.000 palavras)"),
]

FREQUENCIA_PLANO = [
    ("2x_semana", "2x por semana"),
    ("3x_semana", "3x por semana"),
    ("5x_semana", "5x por semana"),
    ("diario", "Diário"),
]


AREAS_JURIDICAS_PADRAO = [
    "Trabalhista",
    "Previdenciário",
    "Empresarial",
    "Família",
    "Consumidor",
    "Tributário",
    "Imobiliário",
    "Bancário",
    "Outra",
]


FORMATOS_REAPROVEITAMENTO: list[tuple[str, str]] = [
    ("blog_artigo", "Artigo para blog"),
    ("linkedin_post", "Post LinkedIn"),
    ("instagram_carrossel", "Carrossel Instagram"),
    ("instagram_reels", "Roteiro Reels"),
    ("posts_curtos", "3 posts curtos"),
    ("newsletter", "Newsletter"),
    ("faq", "FAQ"),
]

MAPA_REAPROVEITAMENTO: dict[str, tuple[str, str]] = {
    "blog_artigo": (CanalConteudo.BLOG, "artigo"),
    "linkedin_post": (CanalConteudo.LINKEDIN, "post"),
    "instagram_carrossel": (CanalConteudo.INSTAGRAM, "carrossel"),
    "instagram_reels": (CanalConteudo.INSTAGRAM, "reels"),
    "newsletter": (CanalConteudo.EMAIL, "newsletter"),
    "faq": (CanalConteudo.BLOG, "faq"),
}


class PlataformaMarketing(models.TextChoices):
    INSTAGRAM = "instagram", "Instagram"
    LINKEDIN = "linkedin", "LinkedIn"
    FACEBOOK = "facebook", "Facebook"
    YOUTUBE = "youtube", "YouTube"
    GOOGLE_ANALYTICS = "google_analytics", "Google Analytics"


class StatusIntegracao(models.TextChoices):
    NAO_CONECTADO = "nao_conectado", "Não conectado"
    PENDENTE = "pendente", "Pendente"
    CONECTADO = "conectado", "Conectado"
