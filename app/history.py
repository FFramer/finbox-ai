"""Contrato de persistencia do historico de conversas."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Protocol

from app.commands import parse_command
from app.parser import ParsedEvent


class HistoryError(RuntimeError):
    """O historico duravel nao conseguiu confirmar uma operacao."""


@dataclass(frozen=True)
class MessageRef:
    conversation_id: int | str
    message_id: int | str
    created: bool


@dataclass(frozen=True)
class HistoryMessage:
    id: int | str
    conversation_id: int | str
    direction: str
    role: str
    kind: str
    content: str | None
    processing_status: str
    delivery_status: str | None
    provider_message_id: str | None
    reply_to_message_id: int | str | None
    occurred_at: datetime
    metadata: dict = field(default_factory=dict)
    ignored_reason: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class ConversationSummary:
    conversation_id: int | str
    summary: str
    covers_through_message_id: int | str
    covered_message_count: int
    model: str
    prompt_version: str


# O que pode virar contexto para o modelo. Comando e ruído; mensagem
# ignorada nunca foi respondida; resposta não entregue o usuário não viu —
# incluí-la faria o modelo acreditar que disse algo que não chegou.
CONTEXT_ROLES = ("user", "assistant")
INBOUND_CONTEXT_STATUS = ("completed", "processing")

# A recusa fixa que o guard emitia antes da fase 0. Ela vive aqui, e so
# aqui, como compatibilidade com o que ja esta gravado.
#
# Duas razoes para nao deixa-la voltar como contexto: o modelo a lia como
# exemplo do proprio comportamento e passava a imita-la; e ela nem sempre
# significou recusa de dominio -- uma falha do provedor produzia este mesmo
# texto, entao como registro de conversa ela e ambigua.
RECUSAS_LEGADAS = (
    "O Finbox responde apenas sobre finanças e documentos financeiros.",
    "Eu fico focado nas suas finanças. Se quiser, posso continuar analisando "
    "sua fatura, seus gastos ou seus investimentos.",
)

# Mantido para quem importava o nome antigo; a primeira e a original.
RECUSA_LEGADA = RECUSAS_LEGADAS[0]

_PADROES_RECUSA_LEGADA = tuple(
    re.compile(r"\s+".join(re.escape(palavra) for palavra in recusa.split()))
    for recusa in RECUSAS_LEGADAS
)


def _espacos_normalizados(texto):
    return " ".join((texto or "").split())


def _e_recusa_legada(conteudo) -> bool:
    """Compara ignorando espacamento: o texto gravado varia na formatacao."""
    normalizado = _espacos_normalizados(conteudo)

    return any(
        normalizado == _espacos_normalizados(recusa)
        for recusa in RECUSAS_LEGADAS
    )


def normalizar_resumo_legado(resumo):
    """Remove a recusa legada de um resumo ja persistido.

    O banco nao muda; a limpeza acontece em toda leitura. Sem isto o texto
    contaminado continuaria chegando ao prompt de resposta e, pior, seria
    reciclado para dentro do proximo resumo rolling.
    """
    if resumo is None:
        return None

    for padrao in _PADROES_RECUSA_LEGADA:
        resumo = padrao.sub("", resumo)

    return resumo


def is_context_eligible(message: HistoryMessage) -> bool:
    if message.role not in CONTEXT_ROLES:
        return False
    if message.kind == "command":
        return False
    if not (message.content or "").strip():
        return False
    if _e_recusa_legada(message.content):
        return False
    if message.direction == "inbound":
        return message.processing_status in INBOUND_CONTEXT_STATUS
    if message.direction == "outbound":
        return message.delivery_status == "sent"
    return False


class HistoryStore(Protocol):
    async def record_inbound(self, event: ParsedEvent) -> MessageRef: ...

    async def mark_inbound(
        self, ref: MessageRef, status: str, reason: str | None = None
    ) -> None: ...

    async def record_outbound(
        self,
        inbound: MessageRef,
        text: str,
        *,
        provider_message_id: str | None,
        delivered: bool,
    ) -> MessageRef: ...

    async def record_transactions(
        self, ref: MessageRef, transacoes
    ) -> int: ...

    async def transactions_for_conversation(
        self, conversation_id: int | str, limit: int = 200
    ) -> list[dict]: ...

    async def recent_messages(
        self, conversation_id: int | str, limit: int = 20
    ) -> list[HistoryMessage]: ...

    async def recent_eligible(
        self,
        conversation_id: int | str,
        *,
        up_to_id: int | str | None = None,
        limit: int = 20,
    ) -> list[HistoryMessage]: ...

    async def eligible_after(
        self,
        conversation_id: int | str,
        *,
        after_id: int | str | None,
        limit: int,
    ) -> list[HistoryMessage]: ...

    async def conversation_summary(
        self, conversation_id: int | str
    ) -> ConversationSummary | None: ...

    async def save_summary(
        self,
        conversation_id: int | str,
        *,
        summary: str,
        covers_through_message_id: int | str,
        covered_message_count: int,
        model: str,
        prompt_version: str,
        expected_previous_message_id: int | str | None,
    ) -> bool: ...

    async def reset_conversation(self, conversation_id: int | str) -> int: ...

    async def fail_stuck_processing(self, older_than: datetime) -> int: ...


def document_metadata(event: ParsedEvent) -> dict:
    if event.documento is None:
        return {}
    return {
        "document": {
            "name": event.documento.nome,
            "mimetype": event.documento.mimetype,
            "size": event.documento.tamanho,
        }
    }


def _iso_date(valor):
    """A data vem do modelo: pode ser '2026-08-02', '02 AGO' ou vazia."""
    try:
        return date.fromisoformat((valor or "").strip()).isoformat()
    except ValueError:
        return None


def transaction_rows(transacoes) -> list[dict]:
    """Converte as transacoes extraidas em linhas prontas para o banco.

    A validacao fica aqui, no Python, e nao no cast do Postgres: uma data
    que o modelo inventou viraria erro de SQL e abortaria a gravacao da
    fatura inteira. Perder a data de uma linha custa menos que perder 25.
    """
    return [
        {
            "occurred_on": _iso_date(transacao.data),
            "description": transacao.descricao,
            # Decimal nao serializa em JSON; str preserva a exatidao que
            # float perderia no caminho ate o numeric do Postgres.
            "amount": str(transacao.valor),
            "category": transacao.categoria or "Outros",
            "position": posicao,
        }
        for posicao, transacao in enumerate(transacoes, 1)
    ]


def identity_type(external_id: str | None) -> str:
    if not external_id:
        return "unknown"
    if external_id.endswith("@lid"):
        return "lid"
    if external_id.endswith("@s.whatsapp.net"):
        return "phone"
    return "unknown"


def message_kind(event: ParsedEvent) -> str:
    if event.documento is not None:
        return "document"
    if parse_command(event.text) is not None:
        return "command"
    return "text"


class InMemoryHistory:
    """Implementacao local com a mesma idempotencia do banco."""

    def __init__(self, principal_key: str = "primary"):
        self.principal_key = principal_key
        self.principals: dict[str, dict] = {}
        self.identities: dict[tuple[str, str], dict] = {}
        self.conversations: dict[tuple[str, str, str], dict] = {}
        self.messages: dict[int, HistoryMessage] = {}
        self.summaries: dict[str, ConversationSummary] = {}
        self.transactions: dict[int, list[dict]] = {}
        self._provider_ids: dict[tuple[int, str], int] = {}
        self._next_principal_id = 1
        self._next_identity_id = 1
        self._next_conversation_id = 1
        self._next_message_id = 1
        self._lock = asyncio.Lock()

    def _ensure_principal(self, event: ParsedEvent) -> int:
        principal = self.principals.get(self.principal_key)
        if principal is None:
            principal = {
                "id": self._next_principal_id,
                "external_key": self.principal_key,
                "display_name": event.push_name,
            }
            self._next_principal_id += 1
            self.principals[self.principal_key] = principal
        elif event.push_name:
            principal["display_name"] = event.push_name
        return principal["id"]

    def _ensure_identity(self, principal_id: int, event: ParsedEvent) -> int | None:
        if not event.author_id:
            return None
        key = ("whatsapp", event.author_id)
        identity = self.identities.get(key)
        if identity is None:
            identity = {
                "id": self._next_identity_id,
                "principal_id": principal_id,
                "channel": "whatsapp",
                "external_id": event.author_id,
                "identity_type": identity_type(event.author_id),
                "display_name": event.push_name,
            }
            self._next_identity_id += 1
            self.identities[key] = identity
        elif event.push_name:
            identity["display_name"] = event.push_name
        return identity["id"]

    def _ensure_conversation(self, principal_id: int, event: ParsedEvent) -> int:
        key = ("whatsapp", event.instance or "default", event.chat_id)
        conversation = self.conversations.get(key)
        if conversation is None:
            conversation = {
                "id": self._next_conversation_id,
                "principal_id": principal_id,
                "channel": "whatsapp",
                "provider_instance": event.instance or "default",
                "external_chat_id": event.chat_id,
                "is_group": event.is_group,
            }
            self._next_conversation_id += 1
            self.conversations[key] = conversation
        return conversation["id"]

    async def record_inbound(self, event: ParsedEvent) -> MessageRef:
        async with self._lock:
            principal_id = self._ensure_principal(event)
            identity_id = self._ensure_identity(principal_id, event)
            conversation_id = self._ensure_conversation(principal_id, event)

            if event.message_id:
                existing = self._provider_ids.get((conversation_id, event.message_id))
                if existing is not None:
                    return MessageRef(conversation_id, existing, False)

            message_id = self._next_message_id
            self._next_message_id += 1
            metadata = document_metadata(event)
            if identity_id is not None:
                metadata["author_identity_id"] = identity_id

            self.messages[message_id] = HistoryMessage(
                id=message_id,
                conversation_id=conversation_id,
                direction="inbound",
                role="user",
                kind=message_kind(event),
                content=event.text,
                processing_status="received",
                delivery_status=None,
                provider_message_id=event.message_id,
                reply_to_message_id=None,
                occurred_at=event.occurred_at or datetime.now(timezone.utc),
                metadata=metadata,
                created_at=datetime.now(timezone.utc),
            )
            if event.message_id:
                self._provider_ids[(conversation_id, event.message_id)] = message_id
            return MessageRef(conversation_id, message_id, True)

    async def mark_inbound(
        self, ref: MessageRef, status: str, reason: str | None = None
    ) -> None:
        async with self._lock:
            message = self.messages.get(int(ref.message_id))
            if message is None:
                raise HistoryError(f"Mensagem {ref.message_id} nao encontrada")
            self.messages[int(ref.message_id)] = replace(
                message, processing_status=status, ignored_reason=reason
            )

    async def record_outbound(
        self,
        inbound: MessageRef,
        text: str,
        *,
        provider_message_id: str | None,
        delivered: bool,
    ) -> MessageRef:
        async with self._lock:
            conversation_id = int(inbound.conversation_id)
            if provider_message_id:
                existing = self._provider_ids.get((conversation_id, provider_message_id))
                if existing is not None:
                    return MessageRef(conversation_id, existing, False)

            message_id = self._next_message_id
            self._next_message_id += 1
            self.messages[message_id] = HistoryMessage(
                id=message_id,
                conversation_id=conversation_id,
                direction="outbound",
                role="assistant",
                kind="text",
                content=text,
                processing_status="completed",
                delivery_status="sent" if delivered else "failed",
                provider_message_id=provider_message_id,
                reply_to_message_id=inbound.message_id,
                occurred_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )
            if provider_message_id:
                self._provider_ids[(conversation_id, provider_message_id)] = message_id
            return MessageRef(conversation_id, message_id, True)

    async def record_transactions(self, ref: MessageRef, transacoes) -> int:
        """Substitui os lancamentos do documento, nao acrescenta.

        Reprocessar o mesmo evento tem que deixar a fatura igual, nao
        dobrada -- a mesma idempotencia que record_inbound ja garante.
        """
        linhas = transaction_rows(transacoes)

        async with self._lock:
            self.transactions[int(ref.message_id)] = [
                {
                    **linha,
                    "conversation_id": int(ref.conversation_id),
                    "message_id": int(ref.message_id),
                }
                for linha in linhas
            ]

        return len(linhas)

    async def reset_conversation(self, conversation_id) -> int:
        """Apaga a conversa inteira e devolve quantas mensagens sairam.

        Historico, resumo e lancamentos vao juntos: um reset que deixasse
        parte do contexto para tras nao seria reset.
        """
        alvo = int(conversation_id)

        async with self._lock:
            ids = [
                mid for mid, m in self.messages.items()
                if int(m.conversation_id) == alvo
            ]
            for mid in ids:
                del self.messages[mid]
                self.transactions.pop(mid, None)

            self._provider_ids = {
                chave: valor for chave, valor in self._provider_ids.items()
                if chave[0] != alvo
            }
            self.summaries.pop(str(alvo), None)
            self.conversations = {
                chave: conversa for chave, conversa in self.conversations.items()
                if int(conversa["id"]) != alvo
            }

        return len(ids)

    def transactions_for(self, message_id) -> list[dict]:
        return list(self.transactions.get(int(message_id), []))

    async def transactions_for_conversation(
        self, conversation_id, limit: int = 200
    ) -> list[dict]:
        async with self._lock:
            linhas = [
                linha
                for linhas_da_mensagem in self.transactions.values()
                for linha in linhas_da_mensagem
                if linha["conversation_id"] == int(conversation_id)
            ]

        linhas.sort(key=lambda l: (l["message_id"], l["position"]))

        # O corte tira as mais antigas: a fatura recente e a que o usuario
        # esta olhando.
        return linhas[-limit:] if limit > 0 else []

    async def recent_messages(
        self, conversation_id: int | str, limit: int = 20
    ) -> list[HistoryMessage]:
        if limit <= 0:
            return []
        async with self._lock:
            found = [
                message
                for message in self.messages.values()
                if str(message.conversation_id) == str(conversation_id)
            ]
            found.sort(key=lambda item: (item.occurred_at, int(item.id)))
            return found[-limit:]

    def _elegiveis(self, conversation_id: int | str) -> list[HistoryMessage]:
        # O id e a sequencia canonica: occurred_at vem do provedor e pode
        # empatar ou chegar fora de ordem.
        found = [
            message
            for message in self.messages.values()
            if str(message.conversation_id) == str(conversation_id)
            and is_context_eligible(message)
        ]
        found.sort(key=lambda item: int(item.id))
        return found

    async def recent_eligible(
        self,
        conversation_id: int | str,
        *,
        up_to_id: int | str | None = None,
        limit: int = 20,
    ) -> list[HistoryMessage]:
        if limit <= 0:
            return []
        async with self._lock:
            found = self._elegiveis(conversation_id)
            if up_to_id is not None:
                found = [m for m in found if int(m.id) <= int(up_to_id)]
            return found[-limit:]

    async def eligible_after(
        self,
        conversation_id: int | str,
        *,
        after_id: int | str | None,
        limit: int,
    ) -> list[HistoryMessage]:
        if limit <= 0:
            return []
        async with self._lock:
            found = self._elegiveis(conversation_id)
            if after_id is not None:
                found = [m for m in found if int(m.id) > int(after_id)]
            return found[:limit]

    async def conversation_summary(
        self, conversation_id: int | str
    ) -> ConversationSummary | None:
        async with self._lock:
            return self.summaries.get(str(conversation_id))

    async def save_summary(
        self,
        conversation_id: int | str,
        *,
        summary: str,
        covers_through_message_id: int | str,
        covered_message_count: int,
        model: str,
        prompt_version: str,
        expected_previous_message_id: int | str | None,
    ) -> bool:
        """Concorrencia otimista: duas mensagens da mesma conversa podem
        resumir ao mesmo tempo, e o watermark nunca pode retroceder."""
        async with self._lock:
            chave = str(conversation_id)
            atual = self.summaries.get(chave)
            esperado = atual.covers_through_message_id if atual else None

            if str(esperado) != str(expected_previous_message_id):
                return False
            if atual and int(covers_through_message_id) <= int(esperado):
                return False

            self.summaries[chave] = ConversationSummary(
                conversation_id=conversation_id,
                summary=summary,
                covers_through_message_id=covers_through_message_id,
                covered_message_count=covered_message_count,
                model=model,
                prompt_version=prompt_version,
            )
            return True


    async def fail_stuck_processing(self, older_than: datetime) -> int:
        """Marca como falhas as mensagens que ficaram em processing.

        O corte por idade existe para o caso de mais de uma replica: uma
        subida nao pode matar o trabalho em voo da outra.
        """
        async with self._lock:
            presas = [
                message
                for message in self.messages.values()
                if message.processing_status == "processing"
                and (message.created_at or message.occurred_at) < older_than
            ]
            for message in presas:
                self.messages[int(message.id)] = replace(
                    message, processing_status="failed", ignored_reason="orphaned"
                )
            return len(presas)


_fallback_history = InMemoryHistory()


def get_history_store() -> HistoryStore:
    """Dependencia FastAPI; o lifespan substitui por Supabase em producao."""
    return _fallback_history
