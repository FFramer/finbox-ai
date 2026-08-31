"""Fase 2: montagem do contexto e resumo rolling."""

from datetime import datetime, timezone

import pytest

from app import ai, memory
from app.ai import AIError
from app.history import HistoryError, InMemoryHistory
from app.memory import ConversationMemory
from app.parser import ParsedEvent


def event(message_id, texto="Quanto gastei?"):
    return ParsedEvent(
        chat_id="5511999999999@s.whatsapp.net",
        from_me=False,
        is_group=False,
        text=texto,
        message_type="conversation",
        author_id="5511999999999@s.whatsapp.net",
        push_name="Fabio",
        message_id=message_id,
        instance="finbox",
        occurred_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )


async def troca(history, sufixo, texto="Quanto gastei?"):
    """Uma pergunta respondida e entregue -- o par elegivel para contexto."""
    inbound = await history.record_inbound(event(f"MSG-{sufixo}", texto))
    await history.mark_inbound(inbound, "completed")
    await history.record_outbound(
        inbound, f"Resposta {sufixo}", provider_message_id=f"OUT-{sufixo}",
        delivered=True,
    )
    return inbound


async def conversa(trocas=1, texto="Quanto gastei?"):
    """Cria N trocas completas e devolve (history, ultimo_inbound)."""
    history = InMemoryHistory()
    inbound = None
    for i in range(trocas):
        inbound = await troca(history, i, texto)
    return history, inbound


def memoria(history, **kwargs):
    padrao = dict(window=20, guard_window=2, summary_every=2, max_chars=10000)
    return ConversationMemory(history, **{**padrao, **kwargs})


@pytest.mark.asyncio
async def test_contexto_traz_janela_terminando_na_mensagem_atual():
    history, _ = await conversa(trocas=1)
    atual = await history.record_inbound(event("MSG-ATUAL", "e o maior gasto?"))
    await history.mark_inbound(atual, "processing")

    bundle = await memoria(history).build_context(
        atual.conversation_id, atual.message_id, "e o maior gasto?"
    )

    assert [(m.role, m.content) for m in bundle.messages] == [
        ("user", "Quanto gastei?"),
        ("assistant", "Resposta 0"),
        ("user", "e o maior gasto?"),
    ]
    assert bundle.degraded is False


@pytest.mark.asyncio
async def test_historico_indisponivel_ainda_garante_a_mensagem_atual():
    """Responder sem contexto nao pode virar responder sem a pergunta."""

    class HistoricoQuebrado:
        async def recent_eligible(self, *a, **k):
            raise HistoryError("supabase fora do ar")

        async def conversation_summary(self, *a, **k):
            raise HistoryError("supabase fora do ar")

    bundle = await memoria(HistoricoQuebrado()).build_context(1, 99, "e o total?")

    assert [(m.role, m.content) for m in bundle.messages] == [
        ("user", "e o total?")
    ]
    assert bundle.degraded is True


@pytest.mark.asyncio
async def test_teto_de_caracteres_descarta_as_mais_antigas():
    history, _ = await conversa(trocas=3)
    atual = await history.record_inbound(event("MSG-ATUAL", "e agora?"))
    await history.mark_inbound(atual, "processing")

    bundle = await memoria(history, max_chars=30).build_context(
        atual.conversation_id, atual.message_id, "e agora?"
    )

    assert sum(len(m.content) for m in bundle.messages) <= 30
    assert bundle.messages[-1].content == "e agora?"


@pytest.mark.asyncio
async def test_guard_recebe_so_as_ultimas_trocas():
    history, _ = await conversa(trocas=3)
    atual = await history.record_inbound(event("MSG-ATUAL", "e agora?"))
    await history.mark_inbound(atual, "processing")

    bundle = await memoria(history, guard_window=2).build_context(
        atual.conversation_id, atual.message_id, "e agora?"
    )

    assert len(bundle.guard_messages) == 2
    assert bundle.guard_messages[-1].content == "e agora?"
    assert len(bundle.messages) > 2


@pytest.mark.asyncio
async def test_resumo_nao_roda_antes_do_gatilho(monkeypatch):
    history, _ = await conversa(trocas=1)
    chamou = []
    monkeypatch.setattr(
        memory.ai, "summarize_conversation",
        lambda *a, **k: chamou.append(1),
    )

    aplicado = await memoria(history, summary_every=10).maybe_refresh_summary(
        None, 1
    )

    assert aplicado is False
    assert chamou == []


@pytest.mark.asyncio
async def test_resumo_grava_e_avanca_o_watermark(monkeypatch):
    history, ultimo = await conversa(trocas=2)

    async def resumir(client, anterior, conversa_):
        return "Falou de gastos."

    monkeypatch.setattr(memory.ai, "summarize_conversation", resumir)

    aplicado = await memoria(history, summary_every=2).maybe_refresh_summary(
        None, ultimo.conversation_id
    )
    resumo = await history.conversation_summary(ultimo.conversation_id)

    assert aplicado is True
    assert resumo.summary == "Falou de gastos."
    assert resumo.prompt_version == ai.PROMPT_VERSION_RESUMO
    # O lote e limitado a summary_every + 1 para nao crescer sem teto; o que
    # sobrar entra na proxima passada, ja que isso roda a cada resposta.
    assert int(resumo.covers_through_message_id) == 3
    assert resumo.covered_message_count == 3


@pytest.mark.asyncio
async def test_segunda_passada_continua_de_onde_a_primeira_parou(monkeypatch):
    history, ultimo = await conversa(trocas=2)
    vistos = []

    async def resumir(client, anterior, conversa_):
        vistos.append((anterior, [m.content for m in conversa_]))
        return f"Resumo {len(vistos)}"

    monkeypatch.setattr(memory.ai, "summarize_conversation", resumir)
    mem = memoria(history, summary_every=2)

    await mem.maybe_refresh_summary(None, ultimo.conversation_id)
    await troca(history, 9, "e depois?")
    await mem.maybe_refresh_summary(None, ultimo.conversation_id)
    resumo = await history.conversation_summary(ultimo.conversation_id)

    assert vistos[0][0] is None
    # A segunda passada parte do resumo da primeira e so ve o que veio depois
    # do watermark -- nao rele a conversa inteira.
    assert vistos[1][0] == "Resumo 1"
    assert vistos[1][1] == ["Resposta 1", "e depois?", "Resposta 9"]
    assert resumo.covered_message_count == 6


@pytest.mark.asyncio
async def test_falha_da_ia_mantem_o_resumo_anterior(monkeypatch):
    history, ultimo = await conversa(trocas=2)

    async def explode(client, anterior, conversa_):
        raise AIError("openrouter fora do ar")

    monkeypatch.setattr(memory.ai, "summarize_conversation", explode)

    aplicado = await memoria(history, summary_every=2).maybe_refresh_summary(
        None, ultimo.conversation_id
    )

    assert aplicado is False
    assert await history.conversation_summary(ultimo.conversation_id) is None
