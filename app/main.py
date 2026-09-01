import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
)

from app.adapters.evolution_adapter import (
    EvolutionError,
    baixar_midia,
    get_client,
    get_status,
    send_text,
    set_webhook,
)
from app.adapters.supabase_history_adapter import SupabaseHistory
from app.ai import AIError, answer_financial_question, classify_financial_topic
from app.analise import analisar, responder_sobre, resumir, somar
from app.authorization import is_authorized
from app.commands import Command, parse_command
from app.documento import (
    DocumentoInvalido,
    extrair_texto_pdf,
    validar_documento,
)
from app.config import (
    ADMIN_TOKEN,
    OPENROUTER_BASE_URL,
    EVOLUTION_API_KEY,
    EVOLUTION_API_URL,
    EXPOSE_DOCS,
    STUCK_AFTER_MINUTES,
    SUPABASE_KEY,
    SUPABASE_URL,
    WEBHOOK_SECRET,
    valores_atuais,
    validar,
)
from app.history import (
    HistoryError,
    get_history_store as get_fallback_history,
)
from app.memory import ConversationMemory
from app.parser import parse_event
from app.state import get_state_store

FORA_DO_DOMINIO = (
    'Eu fico focado nas suas finanças. Se quiser, posso continuar analisando '
    'sua fatura, seus gastos ou seus investimentos.'
)
INDISPONIVEL = (
    "Não consegui processar agora. Tente de novo em instantes."
)

estado_app = {}


@asynccontextmanager
async def lifespan(_app):
    # Falhar aqui e melhor que falhar na primeira mensagem: a causa fica
    # visivel no log de subida, não escondida num 502 mais tarde.
    validar(valores_atuais())

    # Um cliente para todo o processo: mantem o pool de conexões e evita
    # refazer o handshake TLS a cada evento recebido.
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                base_url=EVOLUTION_API_URL,
                headers={
                    "apikey": EVOLUTION_API_KEY,
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
        )
        ia = await stack.enter_async_context(
            httpx.AsyncClient(base_url=OPENROUTER_BASE_URL, timeout=60.0)
        )

        history = get_fallback_history()
        if SUPABASE_URL and SUPABASE_KEY:
            database = await stack.enter_async_context(
                httpx.AsyncClient(
                    base_url=f"{SUPABASE_URL}/rest/v1",
                    headers={
                        "apikey": SUPABASE_KEY,
                        "Authorization": f"Bearer {SUPABASE_KEY}",
                        "Content-Type": "application/json",
                    },
                    timeout=15.0,
                )
            )
            history = SupabaseHistory(database)
            print("[history] persistindo no Supabase")
        else:
            # Sem este aviso a aplicação parece saudável enquanto joga o
            # histórico fora — e o sumiço só aparece depois do restart.
            print(
                "[history] SUPABASE_URL/SUPABASE_KEY ausentes: historico "
                "apenas em memoria, perdido no proximo restart"
            )

        await _varrer_orfas(history)

        estado_app.update(client=client, ia=ia, history=history)
        yield
        estado_app.clear()


async def _varrer_orfas(history):
    """Fecha as mensagens que o processo anterior deixou em processing.

    Um restart mata as BackgroundTasks em voo. Sem isto elas ficam presas
    para sempre -- e continuam elegiveis para o contexto, aparecendo em
    toda janela futura daquela conversa.
    """
    corte = datetime.now(timezone.utc) - timedelta(minutes=STUCK_AFTER_MINUTES)
    try:
        total = await history.fail_stuck_processing(corte)
    except HistoryError as exc:
        print(f"[history] varredura de orfas falhou: {exc}")
        return

    print(f"[history] varredura de orfas: {total} mensagem(ns)")


# A aplicação fica atrás de uma URL pública para a Evolution alcancar o
# webhook. Isso expoe tudo o que estiver montado, então a documentação
# interativa (que lista as rotas administrativas) fica fora por padrão.
app = FastAPI(
    title="Finbox AI",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if EXPOSE_DOCS else None,
    redoc_url="/redoc" if EXPOSE_DOCS else None,
    openapi_url="/openapi.json" if EXPOSE_DOCS else None,
)


async def cliente_ia():
    """Cliente do OpenRouter. Timeout maior: geração demora mais que API."""
    ia = estado_app.get("ia")

    if ia is not None:
        yield ia
        return

    async with httpx.AsyncClient(
        base_url=OPENROUTER_BASE_URL, timeout=60.0
    ) as avulso:
        yield avulso


async def cliente_evolution():
    """Cliente compartilhado; cai para um próprio se o lifespan não rodou."""
    client = estado_app.get("client")

    if client is not None:
        yield client
        return

    async for avulso in get_client():
        yield avulso


def get_history_store():
    """Historico do processo; memoria e apenas o fallback local."""
    return estado_app.get("history") or get_fallback_history()


def _confere(recebido, esperado):
    """Compara em tempo constante e nega quando nada foi configurado."""
    if not esperado:
        return False

    return secrets.compare_digest(recebido or "", esperado)


def exigir_segredo_do_webhook(x_finbox_secret: str = Header(default="")):
    """A Evolution envia este cabecalho conforme configurado em webhook.headers.

    Vai no cabecalho, e não na URL, porque a URL fica gravada no banco da
    Evolution e aparece em log.
    """
    if not _confere(x_finbox_secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Segredo do webhook inválido")


def exigir_token_admin(x_admin_token: str = Header(default="")):
    if not _confere(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Token administrativo inválido")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "0.1.0",
    }


@app.get("/evolution-check", dependencies=[Depends(exigir_token_admin)])
async def evolution_check(client=Depends(cliente_evolution)):
    try:
        return await get_status(client)
    except EvolutionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _provider_message_id(body):
    if not isinstance(body, dict):
        return None
    key = body.get("key") or (body.get("message") or {}).get("key") or {}
    return key.get("id")


async def _mark_background(history, inbound, status, reason=None):
    try:
        await history.mark_inbound(inbound, status, reason)
    except HistoryError as exc:
        print(f"[history] falha ao atualizar mensagem: {exc}")


async def _responder(client, history, inbound, evento, texto):
    """Responde no chat de origem.

    Uma falha no envio não pode virar erro HTTP: sem 200 a Evolution
    reenvia o evento, e o comando já foi aplicado.
    """
    provider_message_id = None
    try:
        result = await send_text(client, evento.chat_id, texto)
        provider_message_id = _provider_message_id(result)
        enviado = True
    except EvolutionError as exc:
        print(f"[webhook] falha ao responder: {exc}")
        enviado = False

    registrado = True
    try:
        await history.record_outbound(
            inbound,
            texto,
            provider_message_id=provider_message_id,
            delivered=enviado,
        )
    except HistoryError as exc:
        registrado = False
        print(f"[history] falha ao registrar resposta: {exc}")

    return {
        "received": True,
        "processed": True,
        "reply": texto,
        "reply_enviado": enviado,
        "reply_registrado": registrado,
    }


async def _processar_documento(ia, client, history, inbound, evento):
    """Baixa, le e resume o documento.

    Roda em background como o fluxo de texto, e pelo mesmo motivo: baixar
    um PDF e extrair transações leva bem mais que o timeout do webhook.
    """
    try:
        validar_documento(evento.documento)
        dados = await baixar_midia(client, evento.message_id)
        texto = extrair_texto_pdf(dados)
    except DocumentoInvalido as exc:
        await _responder(client, history, inbound, evento, str(exc))
        await _mark_background(history, inbound, "completed")
        return
    except EvolutionError as exc:
        print(f"[webhook] falha ao baixar documento: {exc}")
        await _responder(
            client, history, inbound, evento, "Não consegui baixar esse arquivo."
        )
        await _mark_background(history, inbound, "completed")
        return

    if not texto:
        await _responder(
            client,
            history,
            inbound,
            evento,
            "Esse PDF não tem texto selecionável. Se for digitalizado, "
            "ainda não consigo ler.",
        )
        await _mark_background(history, inbound, "completed")
        return

    try:
        transacoes = await analisar(ia, texto)
    except AIError as exc:
        print(f"[webhook] falha ao analisar documento: {exc}")
        await _responder(client, history, inbound, evento, INDISPONIVEL)
        await _mark_background(history, inbound, "completed")
        return
    except Exception as exc:
        print(f"[webhook] erro inesperado no documento: {exc}")
        await _mark_background(history, inbound, "failed", "unexpected_error")
        return

    resultado = somar(transacoes)

    # Os lancamentos so existem dentro desta background task: sem gravar,
    # somem com ela e nenhuma pergunta futura alcanca essa fatura. Uma
    # falha aqui fica no log e nao custa a resposta ja calculada.
    try:
        await history.record_transactions(inbound, transacoes)
    except HistoryError as exc:
        print(f"[history] falha ao gravar lancamentos: {exc}")

    extra = None

    # A legenda do anexo e um pedido ("tem cobranca duplicada?"), e o resumo
    # de formato fixo nao tem onde responde-la. Segunda chamada, sobre os
    # numeros que o Python ja calculou.
    if evento.text and transacoes:
        try:
            extra = await responder_sobre(
                ia, resultado, transacoes, evento.text
            )
        except Exception as exc:
            # Um extra que falha nao pode custar a analise que ja deu certo.
            # Fica no log em vez de sumir: o resumo sai do mesmo jeito.
            print(f"[webhook] falha ao responder a legenda: {exc}")

    # Os números do resumo saem do Python, nunca do modelo.
    await _responder(
        client, history, inbound, evento, resumir(resultado, extra)
    )
    await _mark_background(history, inbound, "completed")


async def _processar_com_ia(ia, client, history, inbound, evento):
    """Classifica e responde fora do ciclo da requisição.

    Roda após o 200 já ter sido devolvido, então nada aqui pode levantar:
    uma exceção solta aqui some sem deixar rastro no fluxo HTTP.
    """
    memoria = ConversationMemory(history)
    # Nao levanta: a leitura do contexto degrada para "sem memoria" em vez
    # de derrubar a mensagem, e sempre devolve ao menos a pergunta atual.
    contexto = await memoria.build_context(
        inbound.conversation_id, inbound.message_id, evento.text
    )

    try:
        # A janela curta resolve as referencias imediatas; o resumo preserva
        # o assunto financeiro ativo quando ele comecou antes dessa janela.
        if not await classify_financial_topic(
            ia, contexto.guard_messages, contexto.summary
        ):
            await _responder(client, history, inbound, evento, FORA_DO_DOMINIO)
            await _mark_background(history, inbound, "completed")
            return

        resposta = await answer_financial_question(
            ia, contexto.messages, contexto.summary
        )
    except AIError as exc:
        print(f"[webhook] falha na IA: {exc}")
        await _responder(client, history, inbound, evento, INDISPONIVEL)
        await _mark_background(history, inbound, "completed")
        return
    except Exception as exc:
        print(f"[webhook] erro inesperado ao processar: {exc}")
        await _mark_background(history, inbound, "failed", "unexpected_error")
        return

    entrega = await _responder(client, history, inbound, evento, resposta)
    await _mark_background(history, inbound, "completed")

    # O resumo so avanca sobre o que a memoria conhece. Se a resposta nao
    # foi gravada, o usuario viu algo que o historico nao tem -- mover o
    # watermark aqui perderia essa troca para sempre.
    if entrega["reply_registrado"]:
        await memoria.maybe_refresh_summary(ia, inbound.conversation_id)


async def _mark_before_ack(history, inbound, status, reason=None):
    try:
        await history.mark_inbound(inbound, status, reason)
    except HistoryError as exc:
        raise HTTPException(
            status_code=503,
            detail="Historico indisponivel; o evento deve ser reenviado",
        ) from exc


@app.post("/webhook", dependencies=[Depends(exigir_segredo_do_webhook)])
async def webhook(
    request: Request,
    background: BackgroundTasks,
    estado=Depends(get_state_store),
    client=Depends(cliente_evolution),
    ia=Depends(cliente_ia),
    history=Depends(get_history_store),
):
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Corpo da requisição não e JSON valido"
        ) from exc

    evento = parse_event(payload)
    metadata = payload if isinstance(payload, dict) else {}
    print(
        "[webhook] "
        f"event={metadata.get('event')} "
        f"instance={metadata.get('instance')} "
        f"message_id={getattr(evento, 'message_id', None)}"
    )

    if evento is None:
        return {"received": True, "processed": False, "reason": "nao_e_mensagem"}

    # As respostas do próprio bot voltam como MESSAGES_UPSERT. Sem este
    # guard o Finbox processaria a si mesmo indefinidamente.
    if evento.from_me:
        return {"received": True, "processed": False, "reason": "from_me"}

    if not is_authorized(evento):
        return {"received": True, "processed": False, "reason": "nao_autorizado"}

    try:
        inbound = await history.record_inbound(evento)
    except HistoryError as exc:
        raise HTTPException(
            status_code=503,
            detail="Historico indisponivel; o evento deve ser reenviado",
        ) from exc

    if not inbound.created:
        return {"received": True, "processed": False, "reason": "duplicado"}

    # Comandos rodam mesmo com o bot desligado: se dependessem do estado
    # ativo, um /desativar seria irreversivel por mensagem.
    comando = parse_command(evento.text)

    if comando is Command.ATIVAR:
        estado.set_enabled(True)
        reply = await _responder(
            client, history, inbound, evento, "Finbox ativado."
        )
        await _mark_before_ack(history, inbound, "completed")
        return reply

    if comando is Command.DESATIVAR:
        estado.set_enabled(False)
        reply = await _responder(
            client, history, inbound, evento, "Finbox desativado."
        )
        await _mark_before_ack(history, inbound, "completed")
        return reply

    if not estado.is_enabled():
        await _mark_before_ack(history, inbound, "ignored", "disabled")
        return {"received": True, "processed": False, "reason": "desativado"}

    # Uma fatura já e financeira por definição: passar pelo guard seria
    # uma chamada de modelo a toa, e um falso negativo recusaria o arquivo.
    if evento.documento is not None:
        await _mark_before_ack(history, inbound, "processing")
        background.add_task(
            _processar_documento, ia, client, history, inbound, evento
        )
        return {"received": True, "processed": True, "reason": "documento"}

    if not evento.text:
        await _mark_before_ack(history, inbound, "ignored", "no_text")
        return {"received": True, "processed": False, "reason": "sem_texto"}

    # A IA leva de 2 a 15s. Responder 200 agora e processar depois evita
    # que a Evolution estoure o timeout e reenvie o mesmo evento.
    await _mark_before_ack(history, inbound, "processing")
    background.add_task(_processar_com_ia, ia, client, history, inbound, evento)

    return {"received": True, "processed": True, "reason": "processando"}


@app.post("/setup-webhook", dependencies=[Depends(exigir_token_admin)])
async def setup_webhook(webhook_url: str, client=Depends(cliente_evolution)):
    try:
        return await set_webhook(client, webhook_url)
    except EvolutionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
