from time import perf_counter
import hmac
import json
import logging
import os
import re
from datetime import datetime

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.messages import constants
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ia.services.docs_tenancy import (
    cliente_do_tenant_or_404,
    documento_do_tenant_or_404,
    organization_from_request,
)
from ia.services.document_knowledge import retrieve_tenant_context
from ia.services.secretaria_state import (
    append_secretaria_turns,
    channel_key_chat,
    channel_key_whatsapp,
    load_secretaria_history,
)
from organizacoes.services import CONTEXT_RESOLVED, resolver_organization
from usuarios.models import Cliente, Documentos
from usuarios.document_text import extract_document_text
from usuarios.permissions import pode_baixar_documento

from .agents_juris import JurisprudenciaAI
from .agents import SecretariaAI
from .models import AnaliseJurisprudencia, ContextRag, Pergunta

logger = logging.getLogger(__name__)


def _cliente_do_usuario(request, id):
    return cliente_do_tenant_or_404(request, id)


def _pergunta_do_usuario(request, id):
    organization = organization_from_request(request)
    if organization is None:
        raise Http404()
    return get_object_or_404(
        Pergunta.objects.select_related("cliente", "cliente__organization"),
        id=id,
        cliente__organization=organization,
    )


def _documento_do_usuario(request, id):
    return documento_do_tenant_or_404(request, id)


def _segredo_webhook_valido(request) -> bool:
    esperado = os.environ.get("IA_WEBHOOK_SECRET") or ""
    if not esperado:
        return False
    recebido = request.headers.get("X-Webhook-Secret") or ""
    if not recebido:
        return False
    a = recebido.encode("utf-8")
    b = esperado.encode("utf-8")
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def _organization_for_whatsapp_user(user_id: int):
    User = get_user_model()
    user = User.objects.filter(pk=user_id).first()
    organization, context = resolver_organization(user)
    if context != CONTEXT_RESOLVED or organization is None:
        return None
    return organization


def _usuario_whatsapp_id() -> int | None:
    # TODO MULTI-TENANT:
    # substituir IA_WHATSAPP_USER_ID por configuração vinculada à Organization
    # quando Organization/Membership forem implementados.
    raw = (os.environ.get("IA_WHATSAPP_USER_ID") or "").strip()
    if not raw.isdigit():
        return None
    User = get_user_model()
    pk = int(raw)
    if not User.objects.filter(pk=pk).exists():
        return None
    return pk


def _extrair_mensagem_webhook(data):
    if not isinstance(data, dict):
        return None, None
    phone = data.get("phone")
    bloco = data.get("data")
    if not isinstance(bloco, dict):
        bloco = {}
    chave = bloco.get("key") if isinstance(bloco.get("key"), dict) else {}
    remote = chave.get("remoteJid") or ""
    if not phone and isinstance(remote, str) and remote:
        phone = remote.split("@")[0]
    msg = bloco.get("message") if isinstance(bloco.get("message"), dict) else {}
    ext = msg.get("extendedTextMessage") if isinstance(msg.get("extendedTextMessage"), dict) else {}
    texto = ext.get("text") or msg.get("conversation")
    if not phone or not texto:
        return None, None
    return str(phone), str(texto)


@login_required
def chat(request, id):
    cliente = _cliente_do_usuario(request, id)
    if request.method == "GET":
        return render(request, "chat.html", {"cliente": cliente})
    if request.method == "POST":
        texto = request.POST.get("pergunta") or ""
        pergunta_model = Pergunta.objects.create(pergunta=texto, cliente=cliente)
        return JsonResponse({"id": pergunta_model.id})
    return JsonResponse({"error": "Método não permitido"}, status=405)


@login_required
def stream_resposta(request):
    if request.method != "POST":
        return JsonResponse({"error": "Método não permitido"}, status=405)

    id_pergunta = request.POST.get("id_pergunta")
    pergunta = _pergunta_do_usuario(request, id_pergunta)

    def _normalizar_resposta_secretaria(texto: str) -> str:
        if not texto:
            return texto

        texto_lower = texto.lower()
        padrao_sucesso_horas = re.search(r"(\d{1,2})h\D+(\d{1,2})h", texto_lower)
        padrao_sucesso_data = re.search(
            r"(\d{1,2})\s+de\s+([a-zçãé]+)", texto_lower, flags=re.DOTALL
        )
        if "foi agendad" in texto_lower and padrao_sucesso_horas and padrao_sucesso_data:
            inicio_h, fim_h = padrao_sucesso_horas.groups()
            dia, mes_nome = padrao_sucesso_data.groups()
            meses = {
                "janeiro": "01",
                "fevereiro": "02",
                "marco": "03",
                "março": "03",
                "abril": "04",
                "maio": "05",
                "junho": "06",
                "julho": "07",
                "agosto": "08",
                "setembro": "09",
                "outubro": "10",
                "novembro": "11",
                "dezembro": "12",
            }
            mes = meses.get(mes_nome, "")
            ano = str(datetime.now().year)
            if mes:
                return (
                    f"Agendado com sucesso: {int(dia):02d}/{mes}/{ano} "
                    f"das {int(inicio_h):02d}:00 às {int(fim_h):02d}:00."
                )

        if "indispon" in texto_lower:
            horas = re.findall(r"(\d{1,2})h\D+(\d{1,2})h", texto_lower)
            if horas:
                sugestoes = ", ".join(
                    f"{int(h1):02d}:00-{int(h2):02d}:00" for h1, h2 in horas[:3]
                )
                return f"Horario indisponivel. Sugestoes: {sugestoes}."

        if (
            "confirm" in texto_lower
            and "foi agendad" not in texto_lower
            and "indispon" not in texto_lower
        ):
            return "Posso confirmar este horario? Responda: SIM para confirmar."

        linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
        if not linhas:
            return texto
        return " ".join(linhas[:2])

    def _nome_evento(chunk) -> str:
        ev = getattr(chunk, "event", None)
        if ev is None:
            return ""
        return getattr(ev, "value", str(ev))

    def _conteudo_para_texto(content) -> str:
        """Compatível com streaming OpenAI/Agno: str, lista de partes ou outros tipos."""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            partes = []
            for item in content:
                if isinstance(item, str):
                    partes.append(item)
                elif isinstance(item, dict):
                    txt = item.get("text")
                    if isinstance(txt, str):
                        partes.append(txt)
                    elif isinstance(item.get("content"), str):
                        partes.append(item["content"])
                else:
                    txt = getattr(item, "text", None)
                    if isinstance(txt, str):
                        partes.append(txt)
            return "".join(partes)
        return str(content)

    def _stream():
        texto_run_completed = ""
        try:
            from agno.run.agent import RunEvent

            from .agents import SecretariaAI

            organization = organization_from_request(request)
            sql_org = getattr(pergunta.cliente, "organization", None)
            rag_org = None
            if (
                organization is not None
                and sql_org is not None
                and organization.pk == sql_org.pk
            ):
                rag_org = organization
            knowledge_context = retrieve_tenant_context(
                rag_org, pergunta.pergunta
            )
            conversation_history = load_secretaria_history(
                organization=rag_org,
                user_id=pergunta.cliente.user_id,
                channel_key=channel_key_chat(pergunta.cliente.id),
            )
            agent = SecretariaAI.build_agent(
                user_id=pergunta.cliente.user_id,
                organization=rag_org,
                knowledge_context=knowledge_context,
                conversation_history=conversation_history,
                cliente_id=pergunta.cliente.id,
            )
            stream = agent.run(
                pergunta.pergunta,
                stream=True,
                stream_events=True,
            )
            resposta_completa = []
            for chunk in stream:
                ev = _nome_evento(chunk)

                if ev == RunEvent.run_content.value:
                    texto = _conteudo_para_texto(getattr(chunk, "content", None))
                    if texto:
                        resposta_completa.append(texto)
                elif ev == RunEvent.run_intermediate_content.value:
                    texto = _conteudo_para_texto(getattr(chunk, "content", None))
                    if texto:
                        resposta_completa.append(texto)
                elif ev == RunEvent.run_completed.value:
                    texto_run_completed = _conteudo_para_texto(
                        getattr(chunk, "content", None)
                    )
                elif ev == RunEvent.run_error.value:
                    err_txt = _conteudo_para_texto(getattr(chunk, "content", None))
                    if err_txt:
                        resposta_completa.append(err_txt)
                elif ev == RunEvent.tool_call_completed.value and getattr(
                    chunk, "tool", None
                ):
                    tool = chunk.tool
                    ContextRag.objects.create(
                        content={
                            "output": str(getattr(chunk, "content", "") or ""),
                            "result": getattr(tool, "result", None),
                        },
                        tool_name=tool.tool_name or "",
                        tool_args=tool.tool_args,
                        pergunta=pergunta,
                    )

            texto_final = "".join(resposta_completa).strip()
            if not texto_final and texto_run_completed:
                texto_final = texto_run_completed.strip()
            if not texto_final:
                out = agent.run(pergunta.pergunta, stream=False)
                texto_final = _conteudo_para_texto(
                    getattr(out, "content", None)
                ).strip()

            if texto_final:
                texto_final = _normalizar_resposta_secretaria(texto_final)
                append_secretaria_turns(
                    organization=rag_org,
                    user_id=pergunta.cliente.user_id,
                    channel_key=channel_key_chat(pergunta.cliente.id),
                    user_text=pergunta.pergunta,
                    assistant_text=texto_final,
                    cliente=pergunta.cliente,
                )
                yield texto_final
            else:
                yield (
                    "Não foi possível obter resposta do assistente. "
                    "Tente novamente em instantes."
                )
        except Exception as exc:
            logger.exception(
                "ia_stream_erro pergunta_id=%s tipo=%s",
                pergunta.id,
                type(exc).__name__,
            )
            yield "Não foi possível gerar a resposta."

    response = StreamingHttpResponse(
        _stream(),
        content_type="text/plain; charset=utf-8",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@login_required
def ver_referencias(request, id):
    pergunta = _pergunta_do_usuario(request, id)
    contextos = ContextRag.objects.filter(pergunta=pergunta)
    return render(request, "ver_referencias.html", {
        "pergunta": pergunta,
        "contextos": contextos
    })


@login_required
def analise_jurisprudencia(request, id):
    documento = _documento_do_usuario(request, id)
    analise = AnaliseJurisprudencia.objects.filter(documento=documento).first()
    return render(request, 'analise_jurisprudencia.html', {
        'documento': documento,
        'analise': analise,
        'pode_baixar_documentos': pode_baixar_documento(request.user),
    })


@login_required
def processar_analise(request, id):
    if request.method != "POST":
        return redirect("analise_jurisprudencia", id=id)

    documento = _documento_do_usuario(request, id)
    texto_documento = (documento.content or "").strip()
    if not texto_documento and documento.arquivo:
        # Fallback para documentos antigos enviados antes da extração automática.
        with documento.arquivo.open("rb") as f:
            texto_documento = extract_document_text(f, documento.arquivo.name)
        if texto_documento:
            documento.content = texto_documento
            documento.save(update_fields=["content"])

    if not texto_documento:
        messages.add_message(
            request,
            constants.ERROR,
            "Não foi possível extrair texto deste arquivo. Se for PDF escaneado, converta com OCR e envie novamente.",
        )
        return redirect("analise_jurisprudencia", id=id)

    inicio = perf_counter()
    try:
        analise_ai = JurisprudenciaAI().run(texto_documento)
        indice = int(analise_ai.indice_risco)
        if indice <= 30:
            classificacao = "Baixo"
        elif indice <= 60:
            classificacao = "Médio"
        elif indice <= 80:
            classificacao = "Alto"
        else:
            classificacao = "Crítico"

        tempo_processamento = int(perf_counter() - inicio)
        AnaliseJurisprudencia.objects.update_or_create(
            documento=documento,
            defaults={
                "indice_risco": indice,
                "classificacao": classificacao,
                "erros_coerencia": analise_ai.erros_coerencia,
                "riscos_juridicos": analise_ai.riscos_juridicos,
                "problemas_formatacao": analise_ai.problemas_formatacao,
                "red_flags": analise_ai.red_flags,
                "tempo_processamento": tempo_processamento,
            },
        )
        messages.add_message(request, constants.SUCCESS, "Análise concluída com sucesso.")
    except Exception:
        logger.exception(
            "ia_analise_erro documento_id=%s",
            documento.id,
        )
        messages.add_message(request, constants.ERROR, "Falha na análise.")

    return redirect("analise_jurisprudencia", id=id)


@csrf_exempt
@require_POST
def webhook_whatsapp(request):
    if not _segredo_webhook_valido(request):
        return JsonResponse({"error": "forbidden"}, status=403)

    user_id = _usuario_whatsapp_id()
    if user_id is None:
        logger.warning("ia_webhook_user_id_invalido")
        return JsonResponse({"error": "forbidden"}, status=403)

    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "payload_invalido"}, status=400)

    phone, message = _extrair_mensagem_webhook(data)
    if not phone or not message:
        return JsonResponse({"error": "payload_invalido"}, status=400)

    organization = _organization_for_whatsapp_user(user_id)
    knowledge_context = ""
    conversation_history = ""
    if organization is None:
        logger.info("ia_webhook_rag_skip reason=TENANT_UNRESOLVED")
    else:
        knowledge_context = retrieve_tenant_context(organization, message)
        conversation_history = load_secretaria_history(
            organization=organization,
            user_id=user_id,
            channel_key=channel_key_whatsapp(phone),
        )

    try:
        agent = SecretariaAI.build_agent(
            user_id=user_id,
            organization=organization,
            knowledge_context=knowledge_context,
            conversation_history=conversation_history,
        )
        result = agent.run(message)
        if organization is not None:
            assistant_text = ""
            if result is not None:
                assistant_text = str(getattr(result, "content", "") or "")
            append_secretaria_turns(
                organization=organization,
                user_id=user_id,
                channel_key=channel_key_whatsapp(phone),
                user_text=message,
                assistant_text=assistant_text,
            )
    except Exception:
        logger.exception("ia_webhook_erro user_id=%s", user_id)
        return JsonResponse({"error": "falha_interna"}, status=500)

    logger.info("ia_webhook_ok user_id=%s", user_id)
    return JsonResponse({"ok": True})
