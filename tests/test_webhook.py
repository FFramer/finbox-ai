"""Rota receptora de webhook e registro na Evolution."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import cliente_evolution
from app.main import app
from tests.conftest import HEADERS, HEADERS_ADMIN


@pytest.fixture(autouse=True)
def _limpar_overrides():
    yield
    app.dependency_overrides.clear()


def client_with(handler):
    transport = httpx.MockTransport(handler)

    async def override():
        async with httpx.AsyncClient(
            base_url=config.EVOLUTION_API_URL, transport=transport
        ) as c:
            yield c

    app.dependency_overrides[cliente_evolution] = override
    return TestClient(app, raise_server_exceptions=False)


# --- receptor -------------------------------------------------------------

def test_receptor_sempre_confirma_o_recebimento_para_a_evolution():
    """A Evolution reenvia o evento se nao receber 200."""
    r = TestClient(app).post("/webhook", json={"event": "messages.upsert"}, headers=HEADERS)

    assert r.status_code == 200
    assert r.json()["received"] is True


def test_receptor_registra_so_metadados_seguros_no_terminal(capsys):
    TestClient(app).post(
        "/webhook",
        json={"event": "messages.upsert", "conteudo_secreto": "nao-logar"},
        headers=HEADERS,
    )

    saida = capsys.readouterr().out
    assert "[webhook]" in saida
    assert "messages.upsert" in saida
    assert "nao-logar" not in saida


def test_receptor_devolve_400_em_corpo_nao_json_em_vez_de_quebrar():
    r = TestClient(app, raise_server_exceptions=False).post(
        "/webhook", content="isto nao e json", headers=HEADERS)

    assert r.status_code == 400


def test_receptor_ignora_json_que_nao_e_objeto():
    r = TestClient(app).post("/webhook", json=[], headers=HEADERS)

    assert r.status_code == 200
    assert r.json()["reason"] == "nao_e_mensagem"


# --- registro -------------------------------------------------------------

def test_set_webhook_envia_o_payload_aninhado_sob_a_chave_webhook():
    """A v2 rejeita payload plano com: instance requires property "webhook"."""
    capturado = {}

    def handler(request):
        capturado.update(request.read() and __import__("json").loads(request.read()))
        return httpx.Response(200, json={"webhook": {"enabled": True}})

    r = client_with(handler).post(
        "/setup-webhook", params={"webhook_url": "https://tunel.exemplo/webhook"}, headers=HEADERS_ADMIN)

    assert r.status_code == 200
    assert "webhook" in capturado, "payload precisa ser aninhado sob 'webhook'"
    assert capturado["webhook"]["url"] == "https://tunel.exemplo/webhook"
    assert capturado["webhook"]["events"] == ["MESSAGES_UPSERT"]
    assert capturado["webhook"]["enabled"] is True


def test_setup_webhook_devolve_502_quando_a_evolution_recusa():
    def recusa(request):
        return httpx.Response(400, json={"error": "Bad Request"})

    r = client_with(recusa).post(
        "/setup-webhook", params={"webhook_url": "https://tunel.exemplo/webhook"}, headers=HEADERS_ADMIN)

    assert r.status_code == 502


# --- guard de fromMe na rota ---------------------------------------------

def _evento(from_me, texto="oi"):
    return {
        "event": "messages.upsert",
        "instance": "finbox",
        "data": {
            "key": {
                "remoteJid": "5511999999999@s.whatsapp.net",
                "fromMe": from_me,
            },
            "message": {"conversation": texto},
            "messageType": "conversation",
        },
    }


def test_rota_ignora_a_propria_mensagem_para_nao_entrar_em_loop():
    r = TestClient(app).post("/webhook", json=_evento(from_me=True), headers=HEADERS)

    assert r.status_code == 200
    assert r.json()["processed"] is False
    assert r.json()["reason"] == "from_me"


def test_rota_processa_mensagem_alheia_de_remetente_autorizado(monkeypatch):
    from app import authorization
    monkeypatch.setattr(authorization, "ALLOWED_PHONE", "5511999999999")
    monkeypatch.setattr(authorization, "ALLOWED_LID", "")

    r = TestClient(app).post("/webhook", json=_evento(from_me=False), headers=HEADERS)

    assert r.json().get("reason") != "nao_autorizado"


def test_rota_ignora_evento_que_nao_e_mensagem_nova():
    r = TestClient(app).post("/webhook", json={"event": "connection.update"}, headers=HEADERS)

    assert r.status_code == 200
    assert r.json()["processed"] is False
    assert r.json()["reason"] == "nao_e_mensagem"


# --- whitelist na rota ----------------------------------------------------

def _de(author, texto="oi"):
    return {
        "event": "messages.upsert",
        "instance": "finbox",
        "data": {
            "key": {"remoteJid": author, "fromMe": False},
            "message": {"conversation": texto},
            "messageType": "conversation",
        },
    }


def test_rota_ignora_remetente_fora_da_whitelist(monkeypatch):
    from app import authorization
    monkeypatch.setattr(authorization, "ALLOWED_PHONE", "5511999999999")
    monkeypatch.setattr(authorization, "ALLOWED_LID", "")

    r = TestClient(app).post("/webhook", json=_de("5511888888888@s.whatsapp.net"), headers=HEADERS)

    assert r.status_code == 200
    assert r.json()["processed"] is False
    assert r.json()["reason"] == "nao_autorizado"


def test_rota_processa_remetente_da_whitelist(monkeypatch):
    from app import authorization
    monkeypatch.setattr(authorization, "ALLOWED_PHONE", "5511999999999")
    monkeypatch.setattr(authorization, "ALLOWED_LID", "")

    r = TestClient(app).post("/webhook", json=_de("5511999999999@s.whatsapp.net"), headers=HEADERS)

    assert r.json().get("reason") != "nao_autorizado"


def test_o_guard_de_frommene_vem_antes_da_whitelist(monkeypatch):
    """A resposta do bot sai do numero autorizado; so fromMe a barra."""
    from app import authorization
    monkeypatch.setattr(authorization, "ALLOWED_PHONE", "5511999999999")
    monkeypatch.setattr(authorization, "ALLOWED_LID", "")
    evento = _de("5511999999999@s.whatsapp.net")
    evento["data"]["key"]["fromMe"] = True

    r = TestClient(app).post("/webhook", json=evento, headers=HEADERS)

    assert r.json()["reason"] == "from_me"


def test_set_webhook_registra_o_cabecalho_de_segredo_na_evolution(monkeypatch):
    """Sem isto a Evolution chamaria /webhook sem o segredo e levaria 401."""
    import json as _json

    from app.adapters import evolution_adapter

    monkeypatch.setattr(evolution_adapter, "WEBHOOK_SECRET", "segredo-de-teste")
    capturado = {}

    def handler(request):
        capturado.update(_json.loads(request.read()))
        return httpx.Response(200, json={"webhook": {"enabled": True}})

    client_with(handler).post(
        "/setup-webhook",
        params={"webhook_url": "https://tunel.exemplo/webhook"},
        headers=HEADERS_ADMIN,
    )

    assert capturado["webhook"]["headers"] == {"x-finbox-secret": "segredo-de-teste"}
