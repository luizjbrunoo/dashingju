from time import perf_counter
import re
import json
from datetime import datetime

from django.contrib import messages
from django.contrib.messages import constants
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt

from usuarios.models import Cliente, Documentos
from usuarios.document_text import extract_document_text

from .agents_juris import JurisprudenciaAI
from .agents import SecretariaAI
from .models import AnaliseJurisprudencia, ContextRag, Pergunta



@csrf_exempt
def chat(request, id):
    cliente = get_object_or_404(Cliente, id=id)
    if request.method == "GET":
        return render(request, "chat.html", {"cliente": cliente})
    if request.method == "POST":
        texto = request.POST.get("pergunta") or ""
        pergunta_model = Pergunta.objects.create(pergunta=texto, cliente=cliente)
        return JsonResponse({"id": pergunta_model.id})
    return JsonResponse({"error": "Método não permitido"}, status=405)


@csrf_exempt
def stream_resposta(request):
    if request.method != "POST":
        return JsonResponse({"error": "Método não permitido"}, status=405)

    id_pergunta = request.POST.get("id_pergunta")
    pergunta = get_object_or_404(Pergunta, id=id_pergunta)

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

            agent = SecretariaAI.build_agent(
                session_id=pergunta.cliente.id,
                user_id=pergunta.cliente.user_id,
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
                yield _normalizar_resposta_secretaria(texto_final)
            else:
                yield (
                    "Não foi possível obter resposta do assistente. "
                    "Confira se OPENAI_API_KEY está configurada e válida."
                )
        except Exception as exc:
            yield f"[erro ao gerar resposta: {exc}]"

    response = StreamingHttpResponse(
        _stream(),
        content_type="text/plain; charset=utf-8",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response

def ver_referencias(request, id):
    pergunta = get_object_or_404(Pergunta, id=id)
    contextos = ContextRag.objects.filter(pergunta=pergunta)
    return render(request, "ver_referencias.html", {
        "pergunta": pergunta,
        "contextos": contextos
    })

def analise_jurisprudencia(request, id):
    documento = get_object_or_404(Documentos, id=id)
    analise = AnaliseJurisprudencia.objects.filter(documento=documento).first()
    return render(request, 'analise_jurisprudencia.html', {
        'documento': documento,
        'analise': analise
    })


def processar_analise(request, id):
    if request.method != "POST":
        return redirect("analise_jurisprudencia", id=id)

    documento = get_object_or_404(Documentos, id=id)
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
    except Exception as exc:
        messages.add_message(request, constants.ERROR, f"Falha na análise: {exc}")

    return redirect("analise_jurisprudencia", id=id)

@csrf_exempt
def webhook_whatsapp(request):
    data = json.loads(request.body)
    phone = data.get('phone')
    message = data.get('data').get('key').get('remoteJid').split('@')[0]
    message = data.get('data').get('message').get('extendedTextMessage').get('text')


    agent = SecretariaAI.build_agent(session_id=phone)
    response: RunOutput = agent.run(message)
    print(response.content)
    return JsonResponse({'response': response.content})