"""Adapter do historico para a Data API do Supabase."""

from __future__ import annotations

from datetime import datetime

import httpx

from app.history import (
    ConversationSummary,
    HistoryError,
    HistoryMessage,
    MessageRef,
    RECUSAS_LEGADAS,
    document_metadata,
    identity_type,
    message_kind,
    transaction_rows,
)
from app.parser import ParsedEvent


def _instante(valor):
    if not valor:
        return None
    return datetime.fromisoformat(valor.replace("Z", "+00:00"))


CAMPOS_DO_LANCAMENTO = (
    "message_id,position,occurred_on,description,amount,category"
)

CAMPOS_DA_MENSAGEM = (
    "id,conversation_id,direction,role,kind,content,"
    "processing_status,delivery_status,provider_message_id,"
    "reply_to_message_id,occurred_at,created_at,metadata,ignored_reason"
)

# A elegibilidade vai para o banco, e nao para depois da resposta: filtrar
# no cliente deixaria mensagens descartadas ocupando o LIMIT e empurrando
# as uteis para fora da janela.
FILTRO_ELEGIVEL = (
    ("role", "in.(user,assistant)"),
    ("kind", "neq.command"),
    ("content", "not.is.null"),
    ("content", "neq."),
    # As recusas fixas do guard antigo saem aqui, junto com os demais
    # filtros, pelo mesmo motivo deles: descartadas depois da consulta, elas
    # ocupariam vagas do LIMIT e empurrariam mensagem util para fora da
    # janela. Sao duas porque o texto foi reescrito uma vez.
    *(("content", f"neq.{recusa}") for recusa in RECUSAS_LEGADAS),
    (
        "or",
        "(and(direction.eq.inbound,"
        "processing_status.in.(completed,processing)),"
        "and(direction.eq.outbound,delivery_status.eq.sent))",
    ),
)


class SupabaseHistory:
    def __init__(self, client: httpx.AsyncClient, principal_key: str = "primary"):
        self.client = client
        self.principal_key = principal_key

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = await self.client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise HistoryError(f"Supabase inacessivel ao gravar historico: {exc}") from exc

        if response.is_error:
            raise HistoryError(
                f"Supabase respondeu {response.status_code} em {path}"
            )
        return response

    @staticmethod
    def _one(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError as exc:
            raise HistoryError("Supabase devolveu historico em formato invalido") from exc

        if isinstance(body, list):
            body = body[0] if body else None
        if not isinstance(body, dict):
            raise HistoryError("Supabase nao devolveu a referencia da mensagem")
        return body

    async def record_inbound(self, event: ParsedEvent) -> MessageRef:
        response = await self._request(
            "POST",
            "/rpc/record_inbound_message",
            json={
                "p_principal_key": self.principal_key,
                "p_channel": "whatsapp",
                "p_instance": event.instance or "default",
                "p_chat_id": event.chat_id,
                "p_author_id": event.author_id,
                "p_identity_type": identity_type(event.author_id),
                "p_display_name": event.push_name,
                "p_is_group": event.is_group,
                "p_provider_message_id": event.message_id,
                "p_kind": message_kind(event),
                "p_content": event.text,
                "p_occurred_at": (
                    event.occurred_at.isoformat() if event.occurred_at else None
                ),
                "p_metadata": document_metadata(event),
            },
        )
        row = self._one(response)
        return MessageRef(
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            created=bool(row["created"]),
        )

    async def record_transactions(self, ref: MessageRef, transacoes) -> int:
        """Grava os lancamentos da fatura numa unica chamada.

        As linhas vao prontas do Python (data ja validada, valor em texto
        exato): o cast do Postgres nao pode ser o primeiro lugar a
        descobrir que o modelo inventou uma data.
        """
        response = await self._request(
            "POST",
            "/rpc/record_document_transactions",
            json={
                "p_message_id": int(ref.message_id),
                "p_transactions": transaction_rows(transacoes),
            },
        )

        try:
            total = response.json()
        except ValueError as exc:
            raise HistoryError(
                "Supabase devolveu resposta invalida ao gravar lancamentos"
            ) from exc

        if isinstance(total, list):
            total = total[0] if total else 0

        return int(total or 0)

    async def transactions_for_conversation(
        self, conversation_id, limit: int = 200
    ) -> list[dict]:
        if limit <= 0:
            return []

        response = await self._request(
            "GET",
            "/transactions",
            params=[
                ("conversation_id", f"eq.{conversation_id}"),
                ("select", CAMPOS_DO_LANCAMENTO),
                # desc + limit pega os mais recentes; a ordem util depois e
                # a cronologica, entao invertemos aqui.
                ("order", "message_id.desc,position.desc"),
                ("limit", str(limit)),
            ],
        )

        try:
            linhas = response.json()
        except ValueError as exc:
            raise HistoryError(
                "Supabase devolveu lancamentos em formato invalido"
            ) from exc

        if not isinstance(linhas, list):
            raise HistoryError("Supabase nao devolveu uma lista de lancamentos")

        linhas.reverse()
        return linhas

    async def mark_inbound(
        self, ref: MessageRef, status: str, reason: str | None = None
    ) -> None:
        # Pedimos a linha de volta de proposito: um PATCH que nao casa nada
        # responde 204, e dar isso por sucesso esconderia a mensagem perdida.
        response = await self._request(
            "PATCH",
            "/messages",
            params={"id": f"eq.{ref.message_id}", "select": "id"},
            headers={"Prefer": "return=representation"},
            json={"processing_status": status, "ignored_reason": reason},
        )
        self._one(response)

    async def record_outbound(
        self,
        inbound: MessageRef,
        text: str,
        *,
        provider_message_id: str | None,
        delivered: bool,
    ) -> MessageRef:
        response = await self._request(
            "POST",
            "/rpc/record_outbound_message",
            json={
                "p_conversation_id": inbound.conversation_id,
                "p_reply_to_message_id": inbound.message_id,
                "p_provider_message_id": provider_message_id,
                "p_content": text,
                "p_delivery_status": "sent" if delivered else "failed",
                "p_metadata": {},
            },
        )
        row = self._one(response)
        return MessageRef(
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            created=bool(row["created"]),
        )

    def _mensagens(self, response: httpx.Response) -> list[HistoryMessage]:
        try:
            rows = response.json()
        except ValueError as exc:
            raise HistoryError(
                "Supabase devolveu historico em formato invalido"
            ) from exc
        if not isinstance(rows, list):
            raise HistoryError("Supabase nao devolveu uma lista de mensagens")

        return [
            HistoryMessage(
                id=row["id"],
                conversation_id=row["conversation_id"],
                direction=row["direction"],
                role=row["role"],
                kind=row["kind"],
                content=row.get("content"),
                processing_status=row["processing_status"],
                delivery_status=row.get("delivery_status"),
                provider_message_id=row.get("provider_message_id"),
                reply_to_message_id=row.get("reply_to_message_id"),
                occurred_at=datetime.fromisoformat(
                    row["occurred_at"].replace("Z", "+00:00")
                ),
                metadata=row.get("metadata") or {},
                ignored_reason=row.get("ignored_reason"),
                created_at=_instante(row.get("created_at")),
            )
            for row in rows
        ]

    async def recent_eligible(
        self, conversation_id, *, up_to_id=None, limit: int = 20
    ) -> list[HistoryMessage]:
        if limit <= 0:
            return []
        params = [
            ("conversation_id", f"eq.{conversation_id}"),
            ("select", CAMPOS_DA_MENSAGEM),
            *FILTRO_ELEGIVEL,
            # id e a sequencia canonica; desc + limit pega as mais recentes.
            ("order", "id.desc"),
            ("limit", str(limit)),
        ]
        if up_to_id is not None:
            params.append(("id", f"lte.{up_to_id}"))

        response = await self._request("GET", "/messages", params=params)
        mensagens = self._mensagens(response)
        mensagens.reverse()
        return mensagens

    async def eligible_after(
        self, conversation_id, *, after_id, limit: int
    ) -> list[HistoryMessage]:
        if limit <= 0:
            return []
        params = [
            ("conversation_id", f"eq.{conversation_id}"),
            ("select", CAMPOS_DA_MENSAGEM),
            *FILTRO_ELEGIVEL,
            ("order", "id.asc"),
            ("limit", str(limit)),
        ]
        if after_id is not None:
            params.append(("id", f"gt.{after_id}"))

        response = await self._request("GET", "/messages", params=params)
        return self._mensagens(response)

    async def conversation_summary(
        self, conversation_id
    ) -> ConversationSummary | None:
        response = await self._request(
            "GET",
            "/conversation_summaries",
            params={
                "conversation_id": f"eq.{conversation_id}",
                "select": (
                    "conversation_id,summary,covers_through_message_id,"
                    "covered_message_count,model,prompt_version"
                ),
                "limit": "1",
            },
        )
        try:
            rows = response.json()
        except ValueError as exc:
            raise HistoryError("Supabase devolveu resumo invalido") from exc
        if not rows:
            return None

        row = rows[0]
        return ConversationSummary(
            conversation_id=row["conversation_id"],
            summary=row["summary"],
            covers_through_message_id=row["covers_through_message_id"],
            covered_message_count=row["covered_message_count"],
            model=row["model"],
            prompt_version=row["prompt_version"],
        )

    async def save_summary(
        self,
        conversation_id,
        *,
        summary: str,
        covers_through_message_id,
        covered_message_count: int,
        model: str,
        prompt_version: str,
        expected_previous_message_id,
    ) -> bool:
        """A RPC decide: ela so grava se o watermark ainda for o esperado."""
        response = await self._request(
            "POST",
            "/rpc/save_conversation_summary",
            json={
                "p_conversation_id": conversation_id,
                "p_summary": summary,
                "p_covers_through_message_id": covers_through_message_id,
                "p_covered_message_count": covered_message_count,
                "p_model": model,
                "p_prompt_version": prompt_version,
                "p_expected_previous_message_id": expected_previous_message_id,
            },
        )
        try:
            return bool(response.json())
        except ValueError as exc:
            raise HistoryError("Supabase devolveu resposta invalida") from exc

    async def fail_stuck_processing(self, older_than: datetime) -> int:
        """Usa messages_pending_idx: o indice parcial existe para isso."""
        response = await self._request(
            "PATCH",
            "/messages",
            params={
                "processing_status": "eq.processing",
                "created_at": f"lt.{older_than.isoformat()}",
                "select": "id",
            },
            headers={"Prefer": "return=representation"},
            json={"processing_status": "failed", "ignored_reason": "orphaned"},
        )
        try:
            rows = response.json()
        except ValueError as exc:
            raise HistoryError("Supabase devolveu a varredura invalida") from exc
        return len(rows) if isinstance(rows, list) else 0

    async def recent_messages(
        self, conversation_id: int | str, limit: int = 20
    ) -> list[HistoryMessage]:
        if limit <= 0:
            return []
        response = await self._request(
            "GET",
            "/messages",
            params={
                "conversation_id": f"eq.{conversation_id}",
                "select": (
                    "id,conversation_id,direction,role,kind,content,"
                    "processing_status,delivery_status,provider_message_id,"
                    "reply_to_message_id,occurred_at,metadata,ignored_reason"
                ),
                "order": "occurred_at.desc,id.desc",
                "limit": str(limit),
            },
        )
        try:
            rows = response.json()
        except ValueError as exc:
            raise HistoryError("Supabase devolveu historico em formato invalido") from exc
        if not isinstance(rows, list):
            raise HistoryError("Supabase nao devolveu uma lista de mensagens")

        messages = [
            HistoryMessage(
                id=row["id"],
                conversation_id=row["conversation_id"],
                direction=row["direction"],
                role=row["role"],
                kind=row["kind"],
                content=row.get("content"),
                processing_status=row["processing_status"],
                delivery_status=row.get("delivery_status"),
                provider_message_id=row.get("provider_message_id"),
                reply_to_message_id=row.get("reply_to_message_id"),
                occurred_at=datetime.fromisoformat(
                    row["occurred_at"].replace("Z", "+00:00")
                ),
                metadata=row.get("metadata") or {},
                ignored_reason=row.get("ignored_reason"),
                created_at=_instante(row.get("created_at")),
            )
            for row in rows
        ]
        messages.reverse()
        return messages
