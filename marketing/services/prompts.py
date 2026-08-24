"""Construção de prompts para geração de conteúdo jurídico."""

from __future__ import annotations

from typing import Any

from marketing.choices import CanalConteudo, FORMATOS_POR_CANAL
from marketing.models import ContentProfile


def _formatos_label(canal: str) -> str:
    opcoes = FORMATOS_POR_CANAL.get(canal, [])
    return ", ".join(label for _, label in opcoes) or "—"


def perfil_contexto(profile: ContentProfile | None) -> str:
    if not profile:
        return "Perfil do escritório não configurado."
    partes = []
    if profile.nome_escritorio:
        partes.append(f"Escritório: {profile.nome_escritorio}")
    if profile.descricao:
        partes.append(f"Descrição: {profile.descricao}")
    if profile.areas_atuacao:
        partes.append(f"Áreas de atuação:\n{profile.areas_atuacao}")
    if profile.publico:
        partes.append(f"Público-alvo: {profile.publico}")
    if profile.regiao:
        partes.append(f"Região: {profile.regiao}")
    if profile.tom_voz:
        partes.append(f"Tom de voz preferido: {profile.get_tom_voz_display()}")
    if profile.diferenciais:
        partes.append(f"Diferenciais: {profile.diferenciais}")
    if profile.palavras_preferidas:
        partes.append(f"Palavras preferidas: {profile.palavras_preferidas}")
    if profile.palavras_evitar:
        partes.append(f"Palavras a evitar: {profile.palavras_evitar}")
    if profile.ctas_permitidas:
        partes.append(f"CTAs permitidas: {profile.ctas_permitidas}")
    if profile.observacoes_institucionais:
        partes.append(f"Observações institucionais: {profile.observacoes_institucionais}")
    return "\n".join(partes) if partes else "Perfil do escritório não configurado."


def instrucoes_compliance() -> str:
    return """
REGRAS OBRIGATÓRIAS (marketing jurídico / OAB):
- Linguagem informativa e educativa; nunca prometa resultado garantido.
- Evite sensacionalismo, afirmações absolutas e captação indevida de clientes.
- Não afirme que o conteúdo está juridicamente ou eticamente aprovado.
- CTAs devem ser informativas (ex.: agendar consulta, saiba mais), não garantistas.
- Se citar dados ou prazos legais, indique que podem variar e exigem análise individual.
""".strip()


def prompt_por_canal(canal: str, formato: str, parametros: dict[str, Any], profile: ContentProfile | None) -> str:
    base = f"""
{instrucoes_compliance()}

PERFIL DO ESCRITÓRIO:
{perfil_contexto(profile)}

BRIEFING DO CONTEÚDO:
- Título/tema: {parametros.get('titulo') or parametros.get('tema') or '—'}
- Tema: {parametros.get('tema') or '—'}
- Área jurídica: {parametros.get('area_juridica') or '—'}
- Público: {parametros.get('publico') or '—'}
- Objetivo: {parametros.get('objetivo_label') or parametros.get('objetivo') or '—'}
- Canal: {parametros.get('canal_label') or canal}
- Formato: {parametros.get('formato_label') or formato or '—'}
- Palavra-chave principal: {parametros.get('palavra_chave') or '—'}
- Tom: {parametros.get('tom_label') or parametros.get('tom') or '—'}
- CTA desejada: {parametros.get('cta') or '—'}
""".strip()

    extras: dict[str, str] = {
        CanalConteudo.YOUTUBE: _prompt_youtube(formato, parametros),
        CanalConteudo.INSTAGRAM: _prompt_redes_sociais("Instagram", formato, parametros),
        CanalConteudo.LINKEDIN: _prompt_redes_sociais("LinkedIn", formato, parametros),
        CanalConteudo.FACEBOOK: _prompt_redes_sociais("Facebook", formato, parametros),
        CanalConteudo.BLOG: _prompt_blog(parametros),
        CanalConteudo.EMAIL: _prompt_email(formato, parametros),
    }
    extra = extras.get(canal, _prompt_generico(canal, formato))
    return f"{base}\n\n{extra}"


def _prompt_youtube(formato: str, parametros: dict[str, Any]) -> str:
    duracao = parametros.get("duracao_estimada") or "—"
    return f"""
CANAL: YouTube | Formato: {formato or 'vídeo'}
Duração estimada: {duracao}

Gere conteúdo estruturado incluindo:
- 3 opções de título sugerido
- Gancho (primeiros 30 segundos) — sem sensacionalismo
- Agitação do problema (contexto, consequências)
- Solução/explicação passo a passo
- 3 insights principais
- Chamada para ação
- Roteiro completo para apresentação
- Descrição do vídeo, sugestão de thumbnail em texto, palavras-chave, capítulos, hashtags
- Título SEO e descrição SEO
- 3 ideias de Shorts derivados do vídeo
""".strip()


def _prompt_redes_sociais(rede: str, formato: str, parametros: dict[str, Any]) -> str:
    if formato == "carrossel":
        slides = parametros.get("num_slides") or 8
        return f"""
CANAL: {rede} | Formato: Carrossel ({slides} slides)

Gere slides numerados:
Slide 1 — Gancho
Slide 2 — Contexto
(prossiga até Slide {slides})
Último slide — CTA

Inclua também: legenda, hashtags, sugestão visual, palavras-chave.
""".strip()
    if formato == "reels":
        return f"""
CANAL: {rede} | Formato: Reels

Gere: gancho, cenas numeradas (3+), conclusão, CTA, legenda, sugestão de texto na tela, hashtags.
""".strip()
    return f"""
CANAL: {rede} | Formato: {formato or 'post'}

Gere: título/gancho, texto principal, legenda, CTA, hashtags, sugestão visual, palavras-chave.
""".strip()


def _prompt_blog(parametros: dict[str, Any]) -> str:
    tamanho = parametros.get("tamanho_aproximado") or "médio"
    return f"""
CANAL: Blog | Tamanho aproximado: {tamanho}

Gere: título, título SEO, meta description, introdução, estrutura H1/H2/H3, artigo completo,
perguntas frequentes, CTA, palavras-chave, sugestão de links internos.
""".strip()


def _prompt_email(formato: str, parametros: dict[str, Any]) -> str:
    return f"""
CANAL: E-mail | Formato: {formato or 'newsletter'}

Gere: assunto, 3 alternativas de assunto, preheader, introdução, conteúdo, CTA, encerramento.
""".strip()


def _prompt_generico(canal: str, formato: str) -> str:
    return f"""
CANAL: {canal} | Formatos disponíveis: {_formatos_label(canal)}
Formato selecionado: {formato or '—'}

Gere conteúdo adaptado ao canal e formato, com estrutura clara e CTA informativa.
""".strip()


ACOES_REFINAMENTO: dict[str, str] = {
    "regenerar": "Gere uma nova versão completa do conteúdo, mantendo o briefing original.",
    "melhorar": "Melhore clareza, fluidez e persuasão ética do conteúdo existente.",
    "encurtar": "Encurte o conteúdo mantendo os pontos essenciais e a CTA.",
    "expandir": "Expanda o conteúdo com mais detalhes educativos, exemplos e contexto.",
    "alterar_tom": "Reescreva o conteúdo alterando o tom conforme indicado no briefing.",
}


def prompt_refinamento(
    acao: str,
    conteudo_atual: str,
    parametros: dict[str, Any],
    profile: ContentProfile | None,
) -> str:
    instrucao = ACOES_REFINAMENTO.get(acao, ACOES_REFINAMENTO["melhorar"])
    tom_novo = parametros.get("tom_label") or parametros.get("tom") or ""
    extra_tom = f"\nNovo tom desejado: {tom_novo}" if acao == "alterar_tom" and tom_novo else ""
    return f"""
{instrucoes_compliance()}

PERFIL DO ESCRITÓRIO:
{perfil_contexto(profile)}

AÇÃO SOLICITADA: {instrucao}{extra_tom}

BRIEFING ORIGINAL:
- Canal: {parametros.get('canal_label') or parametros.get('canal') or '—'}
- Formato: {parametros.get('formato_label') or parametros.get('formato') or '—'}
- Tema: {parametros.get('tema') or parametros.get('titulo') or '—'}
- Área jurídica: {parametros.get('area_juridica') or '—'}

CONTEÚDO ATUAL:
{conteudo_atual}

Mantenha a mesma estrutura de seções relevante ao canal/formato.
""".strip()


def prompt_newsletter_de_conteudo(
    conteudo_origem: str,
    titulo_origem: str,
    parametros: dict[str, Any],
    profile: ContentProfile | None,
) -> str:
    return f"""
{instrucoes_compliance()}

PERFIL DO ESCRITÓRIO:
{perfil_contexto(profile)}

TAREFA: Transformar o conteúdo existente abaixo em newsletter/e-mail educativo.
Adapte linguagem, tamanho e estrutura para e-mail. Não copie literalmente.

CONTEÚDO ORIGINAL ({titulo_origem}):
{conteudo_origem}

Gere: assunto, 3 alternativas de assunto, preheader, introdução, conteúdo adaptado, CTA, encerramento.
Público: {parametros.get('publico') or '—'}
""".strip()


def prompt_plano_editorial(
    parametros: dict[str, Any],
    profile: ContentProfile | None,
    temas_existentes: list[str],
) -> str:
    canais = parametros.get("canais") or "Instagram, Blog, LinkedIn, YouTube"
    evitar = "\n".join(f"- {t}" for t in temas_existentes[:30]) if temas_existentes else "Nenhum."
    return f"""
{instrucoes_compliance()}

PERFIL DO ESCRITÓRIO:
{perfil_contexto(profile)}

CRIAR PLANO EDITORIAL:
- Área jurídica: {parametros.get('area_juridica') or '—'}
- Público: {parametros.get('publico') or '—'}
- Objetivo: {parametros.get('objetivo_label') or parametros.get('objetivo') or '—'}
- Período: {parametros.get('periodo_dias')} dias a partir de {parametros.get('data_inicio')}
- Frequência: {parametros.get('frequencia_label') or parametros.get('frequencia') or '—'}
- Canais: {canais}

TEMAS JÁ UTILIZADOS (evitar repetir ou sugerir variações muito similares):
{evitar}

Sugira um calendário distribuído no período. Cada item deve ter data (YYYY-MM-DD), canal, formato, tema, objetivo e título curto.
Varie canais e formatos conforme a frequência.
""".strip()


def prompt_banco_ideias(
    parametros: dict[str, Any],
    profile: ContentProfile | None,
    temas_existentes: list[str],
) -> str:
    evitar = "\n".join(f"- {t}" for t in temas_existentes[:40]) if temas_existentes else "Nenhum."
    return f"""
{instrucoes_compliance()}

PERFIL DO ESCRITÓRIO:
{perfil_contexto(profile)}

SUGERIR IDEIAS DE CONTEÚDO:
- Área jurídica: {parametros.get('area_juridica') or '—'}
- Público: {parametros.get('publico') or '—'}
- Objetivo: {parametros.get('objetivo_label') or parametros.get('objetivo') or '—'}
- Quantidade: {parametros.get('quantidade', 6)} ideias

TEMAS/CONTEÚDOS JÁ EXISTENTES (não repetir assuntos muito semelhantes):
{evitar}

Sugira ideias originais, práticas e adequadas a escritórios de advocacia.
Cada ideia: título, descrição breve, canal sugerido e área jurídica.
""".strip()


INSTRUCOES_REAPROVEITAMENTO = """
IMPORTANTE — REAPROVEITAMENTO:
- NÃO copie o texto literalmente.
- Adapte linguagem, tamanho, estrutura e CTA ao canal/formato de destino.
- Preserve a mensagem jurídica informativa e ética.
""".strip()


def prompt_reaproveitamento(
    conteudo_origem: str,
    titulo_origem: str,
    canal_destino: str,
    formato_destino: str,
    parametros: dict[str, Any],
    profile: ContentProfile | None,
) -> str:
    label_map = dict(FORMATOS_POR_CANAL.get(canal_destino, []))
    formato_label = label_map.get(formato_destino, formato_destino)
    return f"""
{instrucoes_compliance()}

{INSTRUCOES_REAPROVEITAMENTO}

PERFIL DO ESCRITÓRIO:
{perfil_contexto(profile)}

CONTEÚDO ORIGINAL ({titulo_origem}):
{conteudo_origem}

DESTINO:
- Canal: {canal_destino}
- Formato: {formato_label}
- Área jurídica: {parametros.get('area_juridica') or '—'}
- Público: {parametros.get('publico') or '—'}
- Objetivo: {parametros.get('objetivo_label') or parametros.get('objetivo') or '—'}

Gere conteúdo totalmente adaptado ao canal e formato de destino.
""".strip()


def prompt_posts_curtos(
    conteudo_origem: str,
    titulo_origem: str,
    parametros: dict[str, Any],
    profile: ContentProfile | None,
) -> str:
    return f"""
{instrucoes_compliance()}

{INSTRUCOES_REAPROVEITAMENTO}

PERFIL DO ESCRITÓRIO:
{perfil_contexto(profile)}

CONTEÚDO ORIGINAL ({titulo_origem}):
{conteudo_origem}

TAREFA: Crie 3 posts curtos distintos para redes sociais (Instagram/LinkedIn).
Cada post deve ter abordagem, gancho e CTA diferentes. Não repita o mesmo texto.
Área jurídica: {parametros.get('area_juridica') or '—'}
""".strip()
