"""Itens 7 e 10: validacao na subida e cliente HTTP compartilhado."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.main import app


def test_a_aplicacao_nao_sobe_com_configuracao_incompleta(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    monkeypatch.setattr(main, "valores_atuais", lambda: dict(
        config.valores_atuais(), ADMIN_TOKEN=""
    ))

    with pytest.raises(config.ConfigInvalida) as erro:
        with TestClient(app):
            pass

    assert "ADMIN_TOKEN" in str(erro.value)


def test_a_aplicacao_sobe_com_configuracao_completa():
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def test_o_cliente_http_e_o_mesmo_entre_requisicoes():
    """Item 10: um cliente por requisicao refaz handshake TLS toda vez."""
    with TestClient(app) as c:
        c.get("/health")
        primeiro = main.estado_app.get("client")
        c.get("/health")
        segundo = main.estado_app.get("client")

    assert primeiro is not None
    assert primeiro is segundo
    assert isinstance(primeiro, httpx.AsyncClient)
