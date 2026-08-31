"""Item 12: proteger o que fica exposto quando a aplicacao vai para a rede."""

from fastapi.testclient import TestClient

from app import main
from app.main import app
from tests.conftest import HEADERS, SEGREDO

EVENTO = {"event": "connection.update"}


# --- segredo do webhook ---------------------------------------------------

def test_webhook_recusa_requisicao_sem_o_segredo():
    r = TestClient(app, raise_server_exceptions=False).post("/webhook", json=EVENTO)

    assert r.status_code == 401


def test_webhook_recusa_segredo_errado():
    c = TestClient(app, raise_server_exceptions=False)

    r = c.post("/webhook", json=EVENTO, headers={"x-finbox-secret": "errado"})

    assert r.status_code == 401


def test_webhook_aceita_o_segredo_correto():
    c = TestClient(app, raise_server_exceptions=False)

    r = c.post("/webhook", json=EVENTO, headers=HEADERS)

    assert r.status_code == 200


def test_webhook_nega_tudo_se_o_segredo_nao_estiver_configurado(monkeypatch):
    """Sem segredo configurado a rota nao pode ficar aberta."""
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "")
    c = TestClient(app, raise_server_exceptions=False)

    r = c.post("/webhook", json=EVENTO, headers=HEADERS)

    assert r.status_code == 401


# --- rota administrativa --------------------------------------------------

def test_setup_webhook_exige_token_administrativo():
    c = TestClient(app, raise_server_exceptions=False)

    r = c.post("/setup-webhook", params={"webhook_url": "https://x.exemplo/webhook"})

    assert r.status_code == 401


def test_setup_webhook_recusa_token_errado():
    c = TestClient(app, raise_server_exceptions=False)

    r = c.post(
        "/setup-webhook",
        params={"webhook_url": "https://x.exemplo/webhook"},
        headers={"x-admin-token": "errado"},
    )

    assert r.status_code == 401


def test_o_segredo_do_webhook_nao_serve_como_token_administrativo(monkeypatch):
    """Sao papeis diferentes: o do webhook trafega ate a Evolution."""
    monkeypatch.setattr(main, "ADMIN_TOKEN", "token-admin")
    c = TestClient(app, raise_server_exceptions=False)

    r = c.post(
        "/setup-webhook",
        params={"webhook_url": "https://x.exemplo/webhook"},
        headers={"x-admin-token": SEGREDO},
    )

    assert r.status_code == 401


# --- documentacao ---------------------------------------------------------

def test_docs_nao_ficam_publicas_por_padrao():
    c = TestClient(app, raise_server_exceptions=False)

    assert c.get("/docs").status_code == 404
    assert c.get("/redoc").status_code == 404
    assert c.get("/openapi.json").status_code == 404


def test_health_continua_publico():
    assert TestClient(app).get("/health").status_code == 200
