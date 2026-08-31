import pytest

from app import config, main

SEGREDO = "segredo-de-teste"
ADMIN = "token-admin-de-teste"
HEADERS = {"x-finbox-secret": SEGREDO}
HEADERS_ADMIN = {"x-admin-token": ADMIN}


@pytest.fixture(autouse=True)
def _segredos(monkeypatch):
    """Rotas protegidas e validacao de subida rodam com valores conhecidos."""
    for modulo in (main, config):
        monkeypatch.setattr(modulo, "WEBHOOK_SECRET", SEGREDO, raising=False)
        monkeypatch.setattr(modulo, "ADMIN_TOKEN", ADMIN, raising=False)
    monkeypatch.setattr(config, "ALLOWED_LID", "111111111111111", raising=False)
