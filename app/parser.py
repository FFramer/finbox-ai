"""Leitura do evento MESSAGES_UPSERT da Evolution API.

A estrutura aqui vem de payload real capturado da instância, não de exemplo
da documentação. Ver tests/fixtures/messages_upsert_grupo_fromme.json.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

MESSAGE_EVENT = "messages.upsert"
GROUP_SUFFIX = "@g.us"


@dataclass(frozen=True)
class Documento:
    nome: str | None
    mimetype: str | None
    tamanho: int | None


@dataclass(frozen=True)
class ParsedEvent:
    chat_id: str
    from_me: bool
    is_group: bool
    text: str | None
    message_type: str | None
    author_id: str | None
    push_name: str | None
    message_id: str | None = None
    documento: Documento | None = None
    instance: str | None = None
    occurred_at: datetime | None = None


def _extract_occurred_at(value):
    if value is None:
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None

    if timestamp >= 1_000_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _extract_documento(message):
    """A Evolution descreve anexos em documentMessage."""
    doc = message.get("documentMessage")

    if not doc:
        return None

    tamanho = doc.get("fileLength")

    return Documento(
        nome=doc.get("fileName"),
        mimetype=doc.get("mimetype"),
        # fileLength chega como string na v2.
        tamanho=int(tamanho) if tamanho is not None else None,
    )


def _extract_text(message):
    """A Evolution usa chaves diferentes conforme o tipo da mensagem."""
    if "conversation" in message:
        return message["conversation"]

    extended = message.get("extendedTextMessage") or {}
    return extended.get("text")


def parse_event(payload):
    """Devolve ParsedEvent, ou None se não for uma mensagem nova."""
    if not isinstance(payload, dict):
        return None

    if payload.get("event") != MESSAGE_EVENT:
        return None

    data = payload.get("data") or {}
    key = data.get("key") or {}
    chat_id = key.get("remoteJid")

    if not chat_id:
        return None

    return ParsedEvent(
        chat_id=chat_id,
        from_me=bool(key.get("fromMe")),
        is_group=chat_id.endswith(GROUP_SUFFIX),
        text=_extract_text(data.get("message") or {}),
        message_type=data.get("messageType"),
        # Em grupo quem escreveu vem em 'participant'; em conversa direta
        # esse campo não existe e o autor e o próprio remoteJid.
        author_id=key.get("participant") or chat_id,
        push_name=data.get("pushName"),
        message_id=key.get("id"),
        documento=_extract_documento(data.get("message") or {}),
        instance=payload.get("instance"),
        occurred_at=_extract_occurred_at(data.get("messageTimestamp")),
    )
