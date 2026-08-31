"""Memoria de conversa: monta o contexto e mantem o resumo em dia.

Este modulo e proposital mente profundo. O webhook so pede contexto e pede
uma eventual atualizacao do resumo; filtros, janela, teto, fallback e
concorrencia ficam todos aqui dentro.

Quando o RAG entrar, ele vira mais um bloco montado em build_context --
nao uma mudanca no webhook.
"""

from __future__ import annotations

from dataclasses import dataclass

from app import ai
from app.ai import AIError, ConversationMessage
from app.config import (
    GUARD_WINDOW,
    HISTORY_MAX_CHARS,
    HISTORY_WINDOW,
    SUMMARY_EVERY,
)
from app.history import HistoryError


@dataclass(frozen=True)
class ContextBundle:
    messages: list[ConversationMessage]
    guard_messages: list[ConversationMessage]
    summary: str | None
    degraded: bool


class ConversationMemory:
    def __init__(
        self,
        history,
        *,
        window: int = HISTORY_WINDOW,
        guard_window: int = GUARD_WINDOW,
        summary_every: int = SUMMARY_EVERY,
        max_chars: int = HISTORY_MAX_CHARS,
    ):
        self.history = history
        self.window = window
        self.guard_window = guard_window
        self.summary_every = summary_every
        self.max_chars = max_chars

    async def build_context(
        self, conversation_id, current_message_id, current_text
    ) -> ContextBundle:
        """Monta o contexto ate a mensagem atual, inclusive.

        A leitura e best-effort, ao contrario da escrita: se o banco nao
        responder, respondemos sem memoria em vez de derrubar a mensagem.
        """
        resumo = None
        degradado = False

        try:
            # up_to_id fecha a janela na mensagem atual: duas mensagens da
            # mesma conversa processadas em paralelo nao podem se ver.
            brutas = await self.history.recent_eligible(
                conversation_id, up_to_id=current_message_id, limit=self.window
            )
        except HistoryError as exc:
            print(f"[memory] janela indisponivel, respondendo sem contexto: {exc}")
            brutas, degradado = [], True
        else:
            try:
                atual = await self.history.conversation_summary(conversation_id)
                resumo = atual.summary if atual else None
            except HistoryError as exc:
                print(f"[memory] resumo indisponivel: {exc}")

        mensagens = [
            ConversationMessage(m.role, m.content or "") for m in brutas
        ]

        # Normalmente a atual ja esta na janela, porque record_inbound roda
        # antes. Se a leitura falhou, ela precisa entrar na mao: responder
        # sem contexto nao pode virar responder sem a propria pergunta.
        ultima_e_a_atual = brutas and str(brutas[-1].id) == str(current_message_id)
        if not ultima_e_a_atual and (current_text or "").strip():
            mensagens.append(ConversationMessage("user", current_text))

        mensagens = self._cortar(mensagens)
        guard = mensagens[-self.guard_window:] if self.guard_window > 0 else []

        return ContextBundle(mensagens, guard, resumo, degradado)

    def _cortar(self, mensagens):
        """Teto rigido por caracteres: a contagem de mensagens nao protege
        contra uma unica mensagem enorme, como o resumo de uma fatura."""
        total = sum(len(m.content) for m in mensagens)
        while total > self.max_chars and len(mensagens) > 1:
            total -= len(mensagens[0].content)
            mensagens = mensagens[1:]
        return mensagens

    async def maybe_refresh_summary(self, ia, conversation_id) -> bool:
        """Avanca o resumo se acumulou material desde o ultimo watermark.

        Roda depois da resposta entregue e gravada, entao nada aqui pode
        atrasar ou derrubar o que o usuario ja viu.
        """
        try:
            atual = await self.history.conversation_summary(conversation_id)
            anterior = atual.covers_through_message_id if atual else None
            # Pedimos uma a mais que o gatilho: e o suficiente para saber
            # se ele foi atingido, sem uma consulta de contagem separada.
            novas = await self.history.eligible_after(
                conversation_id,
                after_id=anterior,
                limit=self.summary_every + 1,
            )
        except HistoryError as exc:
            print(f"[memory] nao consegui avaliar o resumo: {exc}")
            return False

        if len(novas) <= self.summary_every:
            return False

        conversa = [ConversationMessage(m.role, m.content or "") for m in novas]

        try:
            texto = await ai.summarize_conversation(
                ia, atual.summary if atual else None, conversa
            )
        except AIError as exc:
            print(f"[memory] resumo nao gerado, mantendo o anterior: {exc}")
            return False

        if not texto:
            return False

        try:
            # Concorrencia otimista: se outra mensagem ja avancou o resumo,
            # este perde e sera refeito na proxima. O watermark nao volta.
            return await self.history.save_summary(
                conversation_id,
                summary=texto,
                covers_through_message_id=novas[-1].id,
                covered_message_count=(
                    (atual.covered_message_count if atual else 0) + len(novas)
                ),
                model=str(ai.MODELO_RESPOSTA or "desconhecido"),
                prompt_version=ai.PROMPT_VERSION_RESUMO,
                expected_previous_message_id=anterior,
            )
        except HistoryError as exc:
            print(f"[memory] resumo nao gravado: {exc}")
            return False
