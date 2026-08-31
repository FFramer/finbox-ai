"""Comportamento esperado de GET /evolution-check.

O endpoint precisa refletir o estado real da instancia na Evolution API,
nunca um valor fixo, e traduzir falhas da API externa em respostas claras.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import cliente_evolution
from app.main import app
from tests.conftest import HEADERS_ADMIN

INSTANCE = config.EVOLUTION_INSTANCE


def client_with(handler):
    """TestClient cujo adapter fala com um transporte httpx simulado."""
    transport = httpx.MockTransport(handler)

    async def override():
        async with httpx.AsyncClient(
            base_url=config.EVOLUTION_API_URL,
            transport=transport,
        ) as c:
            yield c

    app.dependency_overrides[cliente_evolution] = override
    return TestClient(app, raise_server_exceptions=False)


def responder(state=None, webhook=None, status=200):
    def handler(request):
        if "connectionState" in request.url.path:
            body = {"instance": {"instanceName": INSTANCE, "state": state}}
            return httpx.Response(status, json=body)
        # a Evolution devolve o literal `null` quando nao ha webhook
        if webhook is None:
            return httpx.Response(
                status, content="null",
                headers={"content-type": "application/json"},
            )
        return httpx.Response(status, json=webhook)

    return handler


@pytest.fixture(autouse=True)
def _limpar_overrides():
    yield
    app.dependency_overrides.clear()


def test_reporta_desconectado_quando_a_instancia_esta_fechada():
    r = client_with(responder(state="close")).get("/evolution-check", headers=HEADERS_ADMIN)

    assert r.status_code == 200
    assert r.json()["connected"] is False
    assert r.json()["state"] == "close"


def test_reporta_conectado_quando_a_instancia_esta_aberta():
    r = client_with(responder(state="open")).get("/evolution-check", headers=HEADERS_ADMIN)

    assert r.status_code == 200
    assert r.json()["connected"] is True
    assert r.json()["state"] == "open"


def test_sinaliza_webhook_ausente_quando_a_evolution_devolve_null():
    r = client_with(responder(state="open", webhook=None)).get("/evolution-check", headers=HEADERS_ADMIN)

    assert r.json()["webhook"]["configured"] is False
    assert r.json()["webhook"]["url"] is None


def test_sinaliza_webhook_presente_com_a_url_configurada():
    hook = {"enabled": True, "url": "https://exemplo.com/hook"}
    r = client_with(responder(state="open", webhook=hook)).get("/evolution-check", headers=HEADERS_ADMIN)

    assert r.json()["webhook"]["configured"] is True
    assert r.json()["webhook"]["url"] == "https://exemplo.com/hook"


def test_devolve_502_quando_a_evolution_esta_inacessivel():
    def cai(request):
        raise httpx.ConnectError("conexao recusada")

    r = client_with(cai).get("/evolution-check", headers=HEADERS_ADMIN)

    assert r.status_code == 502
    assert "inacess" in r.json()["detail"].lower()


def test_devolve_502_e_cita_a_credencial_quando_a_chave_e_rejeitada():
    def nao_autorizado(request):
        return httpx.Response(401, json={"message": "Unauthorized"})

    r = client_with(nao_autorizado).get("/evolution-check", headers=HEADERS_ADMIN)

    assert r.status_code == 502
    assert "credencial" in r.json()["detail"].lower()


def test_identifica_a_instancia_consultada():
    r = client_with(responder(state="close")).get("/evolution-check", headers=HEADERS_ADMIN)

    assert r.json()["instance"] == INSTANCE


def test_devolve_502_quando_a_evolution_responde_com_corpo_ilegivel():
    def corpo_vazio(request):
        return httpx.Response(200, content="")

    r = client_with(corpo_vazio).get("/evolution-check", headers=HEADERS_ADMIN)

    assert r.status_code == 502
    assert "resposta" in r.json()["detail"].lower()


def test_webhook_desabilitado_nao_conta_como_configurado():
    hook = {"enabled": False, "url": "https://exemplo.com/hook"}
    r = client_with(responder(state="open", webhook=hook)).get("/evolution-check", headers=HEADERS_ADMIN)

    assert r.json()["webhook"]["configured"] is False
