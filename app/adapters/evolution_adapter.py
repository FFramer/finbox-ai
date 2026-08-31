import base64

import httpx

from app.config import (
    EVOLUTION_API_URL,
    EVOLUTION_API_KEY,
    EVOLUTION_INSTANCE,
    WEBHOOK_SECRET,
)

TIMEOUT = 15.0


class EvolutionError(Exception):
    """Falha ao falar com a Evolution API, já com mensagem legível."""


async def get_client():
    async with httpx.AsyncClient(
        base_url=EVOLUTION_API_URL,
        headers={
            "apikey": EVOLUTION_API_KEY,
            "Content-Type": "application/json",
        },
        timeout=TIMEOUT,
    ) as client:
        yield client


async def _get(client, path):
    try:
        response = await client.get(path)
    except httpx.TransportError as exc:
        raise EvolutionError(
            f"Evolution API inacessivel em {EVOLUTION_API_URL}: {exc}"
        ) from exc

    if response.status_code in (401, 403):
        raise EvolutionError(
            "Evolution API recusou a credencial "
            "(verifique EVOLUTION_API_KEY)"
        )

    if response.status_code == 404:
        raise EvolutionError(
            f"Instancia '{EVOLUTION_INSTANCE}' nao encontrada "
            "na Evolution API"
        )

    if response.is_error:
        raise EvolutionError(
            f"Evolution API respondeu {response.status_code} para {path}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise EvolutionError(
            f"Resposta ilegivel da Evolution API em {path} "
            "(esperado JSON)"
        ) from exc


async def get_status(client):
    state_body = await _get(
        client, f"/instance/connectionState/{EVOLUTION_INSTANCE}"
    )
    webhook = await _get(client, f"/webhook/find/{EVOLUTION_INSTANCE}")

    state = (state_body or {}).get("instance", {}).get("state")

    return {
        "instance": EVOLUTION_INSTANCE,
        "connected": state == "open",
        "state": state,
        "webhook": {
            "configured": bool(webhook) and webhook.get("enabled", True),
            "url": (webhook or {}).get("url"),
        },
    }


async def set_webhook(client, webhook_url, events=("MESSAGES_UPSERT",)):
    """Registra o webhook na instância.

    A v2 exige o payload aninhado sob a chave "webhook"; o formato plano
    da v1 e recusado com: instance requires property "webhook".
    """
    payload = {
        "webhook": {
            "enabled": True,
            "url": webhook_url,
            "byEvents": False,
            "base64": False,
            "events": list(events),
            # A Evolution repassa estes cabecalhos em cada chamada. E assim
            # que /webhook distingue um evento legitimo de um forjado.
            "headers": {"x-finbox-secret": WEBHOOK_SECRET or ""},
        }
    }

    try:
        response = await client.post(
            f"/webhook/set/{EVOLUTION_INSTANCE}", json=payload
        )
    except httpx.TransportError as exc:
        raise EvolutionError(
            f"Evolution API inacessivel em {EVOLUTION_API_URL}: {exc}"
        ) from exc

    if response.is_error:
        raise EvolutionError(
            f"Evolution API recusou o registro do webhook "
            f"({response.status_code}): {response.text}"
        )

    return response.json()


async def send_text(client, to, text):
    """Envia uma mensagem de texto.

    A v2 espera o payload plano; o formato aninhado da v1
    ({"textMessage": {"text": ...}}) e recusado com
    : instance requires property "text".
    """
    payload = {"number": to, "text": text}

    try:
        response = await client.post(
            f"/message/sendText/{EVOLUTION_INSTANCE}", json=payload
        )
    except httpx.TransportError as exc:
        raise EvolutionError(
            f"Evolution API inacessivel em {EVOLUTION_API_URL}: {exc}"
        ) from exc

    if response.is_error:
        raise EvolutionError(
            f"Evolution API recusou o envio ({response.status_code}): "
            f"{response.text}"
        )

    return response.json()


async def baixar_midia(client, message_id):
    """Baixa a mídia de uma mensagem.

    A v2 espera {"message": {"key": {...}}}; um payload plano falha com
    TypeError: Cannot read properties of undefined (reading 'key').
    """
    payload = {"message": {"key": {"id": message_id}}}

    try:
        response = await client.post(
            f"/chat/getBase64FromMediaMessage/{EVOLUTION_INSTANCE}", json=payload
        )
    except httpx.TransportError as exc:
        raise EvolutionError(
            f"Evolution API inacessivel em {EVOLUTION_API_URL}: {exc}"
        ) from exc

    if response.is_error:
        raise EvolutionError(
            f"Evolution API recusou o download ({response.status_code}): "
            f"{response.text[:200]}"
        )

    try:
        return base64.b64decode(response.json()["base64"])
    except (KeyError, ValueError, TypeError) as exc:
        raise EvolutionError(f"Midia veio em formato inesperado: {exc}") from exc
