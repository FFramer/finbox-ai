import secrets
from contextlib import asynccontextmanager

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
from app.ai import AIError, answer_financial_question, classify_financial_topic
from app.analise import analisar, resumir, somar
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
    WEBHOOK_SECRET,
    valores_atuais,
    validar,
)
from app.parser import parse_event
from app.state import get_state_store

FORA_DO_DOMINIO = (
    "O Finbox responde apenas sobre finanças e documentos financeiros."
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
    async with httpx.AsyncClient(
        base_url=EVOLUTION_API_URL,
        headers={
            "apikey": EVOLUTION_API_KEY,
            "Content-Type": "application/json",
        },
        timeout=15.0,
    ) as client:
        async with httpx.AsyncClient(
            base_url=OPENROUTER_BASE_URL, timeout=60.0
        ) as ia:
            estado_app["client"] = client
            estado_app["ia"] = ia
            yield
            estado_app.clear()


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


async def _responder(client, evento, texto):
    """Responde no chat de origem.

    Uma falha no envio não pode virar erro HTTP: sem 200 a Evolution
    reenvia o evento, e o comando já foi aplicado.
    """
    try:
        await send_text(client, evento.chat_id, texto)
        enviado = True
    except EvolutionError as exc:
        print(f"[webhook] falha ao responder: {exc}")
        enviado = False

    return {
        "received": True,
        "processed": True,
        "reply": texto,
        "reply_enviado": enviado,
    }


async def _processar_documento(ia, client, evento):
    """Baixa, le e resume o documento.

    Roda em background como o fluxo de texto, e pelo mesmo motivo: baixar
    um PDF e extrair transações leva bem mais que o timeout do webhook.
    """
    try:
        validar_documento(evento.documento)
        dados = await baixar_midia(client, evento.message_id)
        texto = extrair_texto_pdf(dados)
    except DocumentoInvalido as exc:
        await _responder(client, evento, str(exc))
        return
    except EvolutionError as exc:
        print(f"[webhook] falha ao baixar documento: {exc}")
        await _responder(client, evento, "Não consegui baixar esse arquivo.")
        return

    if not texto:
        await _responder(
            client,
            evento,
            "Esse PDF não tem texto selecionável. Se for digitalizado, "
            "ainda não consigo ler.",
        )
        return

    try:
        transacoes = await analisar(ia, texto)
    except AIError as exc:
        print(f"[webhook] falha ao analisar documento: {exc}")
        await _responder(client, evento, INDISPONIVEL)
        return
    except Exception as exc:
        print(f"[webhook] erro inesperado no documento: {exc}")
        return

    # Os números do resumo saem do Python, nunca do modelo.
    await _responder(client, evento, resumir(somar(transacoes)))


async def _processar_com_ia(ia, client, evento):
    """Classifica e responde fora do ciclo da requisição.

    Roda após o 200 já ter sido devolvido, então nada aqui pode levantar:
    uma exceção solta aqui some sem deixar rastro no fluxo HTTP.
    """
    try:
        if not await classify_financial_topic(ia, evento.text):
            await _responder(client, evento, FORA_DO_DOMINIO)
            return

        resposta = await answer_financial_question(ia, evento.text)
    except AIError as exc:
        print(f"[webhook] falha na IA: {exc}")
        await _responder(client, evento, INDISPONIVEL)
        return
    except Exception as exc:
        print(f"[webhook] erro inesperado ao processar: {exc}")
        return

    await _responder(client, evento, resposta)


@app.post("/webhook", dependencies=[Depends(exigir_segredo_do_webhook)])
async def webhook(
    request: Request,
    background: BackgroundTasks,
    estado=Depends(get_state_store),
    client=Depends(cliente_evolution),
    ia=Depends(cliente_ia),
):
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Corpo da requisição não e JSON valido"
        ) from exc

    print("\n--- NOVO EVENTO EVOLUTION ---")
    print(payload)
    print("-----------------------------\n")

    evento = parse_event(payload)

    if evento is None:
        return {"received": True, "processed": False, "reason": "nao_e_mensagem"}

    # As respostas do próprio bot voltam como MESSAGES_UPSERT. Sem este
    # guard o Finbox processaria a si mesmo indefinidamente.
    if evento.from_me:
        return {"received": True, "processed": False, "reason": "from_me"}

    if not is_authorized(evento):
        return {"received": True, "processed": False, "reason": "nao_autorizado"}

    # Comandos rodam mesmo com o bot desligado: se dependessem do estado
    # ativo, um /desativar seria irreversivel por mensagem.
    comando = parse_command(evento.text)

    if comando is Command.ATIVAR:
        estado.set_enabled(True)
        return await _responder(client, evento, "Finbox ativado.")

    if comando is Command.DESATIVAR:
        estado.set_enabled(False)
        return await _responder(client, evento, "Finbox desativado.")

    if not estado.is_enabled():
        return {"received": True, "processed": False, "reason": "desativado"}

    # Uma fatura já e financeira por definição: passar pelo guard seria
    # uma chamada de modelo a toa, e um falso negativo recusaria o arquivo.
    if evento.documento is not None:
        background.add_task(_processar_documento, ia, client, evento)
        return {"received": True, "processed": True, "reason": "documento"}

    if not evento.text:
        return {"received": True, "processed": False, "reason": "sem_texto"}

    # A IA leva de 2 a 15s. Responder 200 agora e processar depois evita
    # que a Evolution estoure o timeout e reenvie o mesmo evento.
    background.add_task(_processar_com_ia, ia, client, evento)

    return {"received": True, "processed": True, "reason": "processando"}


@app.post("/setup-webhook", dependencies=[Depends(exigir_token_admin)])
async def setup_webhook(webhook_url: str, client=Depends(cliente_evolution)):
    try:
        return await set_webhook(client, webhook_url)
    except EvolutionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
