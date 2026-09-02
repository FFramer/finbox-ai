"""Fase 2 no fluxo real: o webhook monta contexto e mantem o resumo."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import authorization, main, memory
from app.history import HistoryError, InMemoryHistory
from app.main import app, cliente_evolution, get_history_store
from app.state import InMemoryBotState, get_state_store
from tests.conftest import HEADERS

AUTOR = "5511999999999@s.whatsapp.net"


def payload(texto, message_id):
    return {
        "event": "messages.upsert",
        "instance": "finbox",
        "data": {
            "key": {"remoteJid": AUTOR, "fromMe": False, "id": message_id},
            "message": {"conversation": texto},
            "messageType": "conversation",
            "messageTimestamp": 1788134400,
            "pushName": "Fabio",
        },
    }


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(authorization, "ALLOWED_PHONE", "5511999999999")
    monkeypatch.setattr(authorization, "ALLOWED_LID", "")
    monkeypatch.setattr(authorization, "ALLOWED_GROUP_ID", "")

    history = InMemoryHistory()
    visto = {"resposta": []}

    async def responder(ia, conversa, resumo=None):
        visto["resposta"].append(
            ([(m.role, m.content) for m in conversa], resumo)
        )
        return "Resposta do modelo"

    monkeypatch.setattr(main, "answer_financial_question", responder)

    def handler(request):
        return httpx.Response(201, json={"key": {"id": "OUT-EVO"}})

    async def evolution():
        async with httpx.AsyncClient(
            base_url="https://evo.example",
            transport=httpx.MockTransport(handler),
        ) as client:
            yield client

    app.dependency_overrides[get_history_store] = lambda: history
    app.dependency_overrides[get_state_store] = lambda: InMemoryBotState(True)
    app.dependency_overrides[cliente_evolution] = evolution
    yield TestClient(app), history, visto
    app.dependency_overrides.clear()


def test_a_segunda_mensagem_chega_ao_modelo_com_a_primeira_troca(cliente):
    client, _, visto = cliente

    client.post("/webhook", json=payload("Analise minha fatura", "M1"), headers=HEADERS)
    client.post("/webhook", json=payload("e o maior gasto?", "M2"), headers=HEADERS)

    conversa, _ = visto["resposta"][1]
    assert conversa == [
        ("user", "Analise minha fatura"),
        ("assistant", "Resposta do modelo"),
        ("user", "e o maior gasto?"),
    ]


def test_a_mensagem_atual_nao_aparece_duplicada(cliente):
    """Ela ja vem na janela porque record_inbound roda antes."""
    client, _, visto = cliente

    client.post("/webhook", json=payload("Quanto gastei?", "M1"), headers=HEADERS)

    conversa, _ = visto["resposta"][0]
    assert conversa == [("user", "Quanto gastei?")]


def test_o_resumo_nao_avanca_se_a_resposta_nao_foi_gravada(cliente, monkeypatch):
    """O usuario viu uma resposta que a memoria nao conhece: avancar o
    watermark aqui perderia essa troca para sempre."""
    client, history, _ = cliente
    tentou = []

    async def falha_ao_gravar(*a, **k):
        raise HistoryError("supabase fora do ar")

    async def espia(self, ia, conversation_id):
        tentou.append(conversation_id)
        return False

    monkeypatch.setattr(history, "record_outbound", falha_ao_gravar)
    monkeypatch.setattr(memory.ConversationMemory, "maybe_refresh_summary", espia)

    client.post("/webhook", json=payload("Quanto gastei?", "M1"), headers=HEADERS)

    assert tentou == []


def test_o_resumo_e_avaliado_apos_a_resposta_gravada(cliente, monkeypatch):
    client, _, _ = cliente
    tentou = []

    async def espia(self, ia, conversation_id):
        tentou.append(conversation_id)
        return False

    monkeypatch.setattr(memory.ConversationMemory, "maybe_refresh_summary", espia)

    client.post("/webhook", json=payload("Quanto gastei?", "M1"), headers=HEADERS)

    assert len(tentou) == 1


def test_o_resumo_guardado_chega_ao_modelo(cliente):
    client, history, visto = cliente

    client.post("/webhook", json=payload("Quanto gastei?", "M1"), headers=HEADERS)
    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        history.save_summary(
            1,
            summary="Ja falamos de fatura.",
            covers_through_message_id=1,
            covered_message_count=1,
            model="m",
            prompt_version="v1",
            expected_previous_message_id=None,
        )
    )
    client.post("/webhook", json=payload("e agora?", "M2"), headers=HEADERS)

    _, resumo = visto["resposta"][1]
    assert resumo == "Ja falamos de fatura."
