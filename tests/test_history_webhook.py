import httpx
import pytest
from fastapi.testclient import TestClient

from app import authorization
from app.history import HistoryError, InMemoryHistory
from app.main import app, cliente_evolution, get_history_store
from app.state import InMemoryBotState, get_state_store
from tests.conftest import HEADERS

AUTHOR = "5511999999999@s.whatsapp.net"


def payload(text="/ativar", message_id="MSG-1", author=AUTHOR):
    return {
        "event": "messages.upsert",
        "instance": "finbox",
        "data": {
            "key": {
                "remoteJid": author,
                "fromMe": False,
                "id": message_id,
            },
            "message": {"conversation": text},
            "messageType": "conversation",
            "messageTimestamp": 1788134400,
            "pushName": "Fabio",
        },
    }


@pytest.fixture
def history_client(monkeypatch):
    monkeypatch.setattr(authorization, "ALLOWED_PHONE", "5511999999999")
    monkeypatch.setattr(authorization, "ALLOWED_LID", "")
    monkeypatch.setattr(authorization, "ALLOWED_GROUP_ID", "")
    history = InMemoryHistory()
    state = InMemoryBotState(enabled=False)
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(201, json={"key": {"id": "OUT-1"}})

    async def evolution():
        async with httpx.AsyncClient(
            base_url="https://evo.example",
            transport=httpx.MockTransport(handler),
        ) as client:
            yield client

    app.dependency_overrides[get_history_store] = lambda: history
    app.dependency_overrides[get_state_store] = lambda: state
    app.dependency_overrides[cliente_evolution] = evolution
    yield TestClient(app), history, sent
    app.dependency_overrides.clear()


def test_webhook_persiste_entrada_e_resposta_ligadas(history_client):
    client, history, _ = history_client

    response = client.post("/webhook", json=payload(), headers=HEADERS)

    assert response.status_code == 200
    messages = list(history.messages.values())
    assert [message.direction for message in messages] == ["inbound", "outbound"]
    assert messages[0].kind == "command"
    assert messages[0].processing_status == "completed"
    assert messages[1].reply_to_message_id == messages[0].id
    assert messages[1].provider_message_id == "OUT-1"


def test_reenvio_da_evolution_nao_executa_o_comando_duas_vezes(history_client):
    client, history, sent = history_client

    first = client.post("/webhook", json=payload(), headers=HEADERS)
    duplicate = client.post("/webhook", json=payload(), headers=HEADERS)

    assert first.status_code == 200
    assert duplicate.json()["reason"] == "duplicado"
    assert len(sent) == 1
    assert len(history.messages) == 2


def test_remetente_nao_autorizado_nao_vai_para_o_historico(history_client):
    client, history, _ = history_client

    response = client.post(
        "/webhook",
        json=payload(author="5511888888888@s.whatsapp.net"),
        headers=HEADERS,
    )

    assert response.json()["reason"] == "nao_autorizado"
    assert history.messages == {}


def test_sem_confirmacao_do_historico_webhook_pede_reenvio(
    history_client,
):
    client, _, sent = history_client

    class FailingHistory:
        async def record_inbound(self, event):
            raise HistoryError("database offline")

    app.dependency_overrides[get_history_store] = lambda: FailingHistory()

    response = client.post("/webhook", json=payload(), headers=HEADERS)

    assert response.status_code == 503
    assert sent == []
