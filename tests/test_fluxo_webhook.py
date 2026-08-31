"""Ordem dos guards e efeito dos comandos na rota /webhook."""

import pytest
from fastapi.testclient import TestClient

from app import authorization, main
from app.main import app
from tests.conftest import HEADERS, HEADERS_ADMIN
from app.state import InMemoryBotState, get_state_store

AUTORIZADO = "5511999999999@s.whatsapp.net"


@pytest.fixture
def cliente(monkeypatch):
    def montar(enabled=True):
        monkeypatch.setattr(authorization, "ALLOWED_PHONE", "5511999999999")
        monkeypatch.setattr(authorization, "ALLOWED_LID", "")
        monkeypatch.setattr(authorization, "ALLOWED_GROUP_ID", "")
        estado = InMemoryBotState(enabled=enabled)
        app.dependency_overrides[get_state_store] = lambda: estado
        return TestClient(app), estado

    yield montar
    app.dependency_overrides.clear()


def msg(texto, autor=AUTORIZADO):
    return {
        "event": "messages.upsert",
        "instance": "finbox",
        "data": {
            "key": {"remoteJid": autor, "fromMe": False},
            "message": {"conversation": texto},
            "messageType": "conversation",
        },
    }


# --- comandos -------------------------------------------------------------

def test_ativar_liga_o_bot(cliente):
    c, estado = cliente(enabled=False)

    r = c.post("/webhook", json=msg("/ativar"), headers=HEADERS)

    assert estado.is_enabled() is True
    assert r.json()["reply"] == "Finbox ativado."


def test_desativar_desliga_o_bot(cliente):
    c, estado = cliente(enabled=True)

    r = c.post("/webhook", json=msg("/desativar"), headers=HEADERS)

    assert estado.is_enabled() is False
    assert r.json()["reply"] == "Finbox desativado."


def test_ativar_funciona_mesmo_com_o_bot_desligado(cliente):
    """Sem isto o bot desativado nunca mais poderia ser religado."""
    c, estado = cliente(enabled=False)

    c.post("/webhook", json=msg("/ativar"), headers=HEADERS)

    assert estado.is_enabled() is True


# --- estado desligado -----------------------------------------------------

def test_mensagem_comum_e_ignorada_com_o_bot_desligado(cliente):
    c, _ = cliente(enabled=False)

    r = c.post("/webhook", json=msg("quanto gastei?"), headers=HEADERS)

    assert r.json()["processed"] is False
    assert r.json()["reason"] == "desativado"


def test_mensagem_comum_passa_do_estado_com_o_bot_ligado(cliente):
    c, _ = cliente(enabled=True)

    r = c.post("/webhook", json=msg("quanto gastei?"), headers=HEADERS)

    assert r.json().get("reason") != "desativado"


# --- comando nao autorizado -----------------------------------------------

def test_estranho_nao_consegue_desativar_o_bot(cliente):
    """O comando so vale depois da whitelist."""
    c, estado = cliente(enabled=True)

    r = c.post("/webhook", json=msg("/desativar", autor="5511888888888@s.whatsapp.net"), headers=HEADERS)

    assert estado.is_enabled() is True
    assert r.json()["reason"] == "nao_autorizado"


def test_o_proprio_bot_nao_dispara_comando(cliente):
    """A resposta do bot sai do numero autorizado; so fromMe a barra."""
    c, estado = cliente(enabled=True)
    evento = msg("/desativar")
    evento["data"]["key"]["fromMe"] = True

    c.post("/webhook", json=evento, headers=HEADERS)

    assert estado.is_enabled() is True


# --- resposta chegando no WhatsApp ----------------------------------------

def cliente_com_envio(monkeypatch, handler, enabled=True):
    import httpx
    from app.main import cliente_evolution

    monkeypatch.setattr(authorization, "ALLOWED_PHONE", "5511999999999")
    monkeypatch.setattr(authorization, "ALLOWED_LID", "")
    monkeypatch.setattr(authorization, "ALLOWED_GROUP_ID", "")
    estado = InMemoryBotState(enabled=enabled)
    app.dependency_overrides[get_state_store] = lambda: estado

    async def override():
        async with httpx.AsyncClient(
            base_url="https://evo.exemplo",
            transport=httpx.MockTransport(handler),
        ) as c:
            yield c

    app.dependency_overrides[cliente_evolution] = override
    return TestClient(app, raise_server_exceptions=False), estado


def test_confirmacao_do_ativar_e_enviada_para_o_chat_de_origem(monkeypatch):
    import json
    import httpx
    enviado = {}

    def h(request):
        enviado.update(json.loads(request.read()))
        return httpx.Response(201, json={"key": {"id": "X"}})

    c, _ = cliente_com_envio(monkeypatch, h, enabled=False)
    c.post("/webhook", json=msg("/ativar"), headers=HEADERS)

    assert enviado["number"] == AUTORIZADO
    assert enviado["text"] == "Finbox ativado."
    app.dependency_overrides.clear()


def test_falha_no_envio_nao_derruba_o_webhook(monkeypatch):
    """Sem 200 a Evolution reenviaria o evento em loop."""
    import httpx

    def h(request):
        return httpx.Response(500, json={"erro": "indisponivel"})

    c, estado = cliente_com_envio(monkeypatch, h, enabled=False)
    r = c.post("/webhook", json=msg("/ativar"), headers=HEADERS)

    assert r.status_code == 200
    assert estado.is_enabled() is True, "o estado muda mesmo se a confirmacao falhar"
    assert r.json()["reply_enviado"] is False
    app.dependency_overrides.clear()


# --- camada 3: guard de financas ------------------------------------------

def cliente_com_ia(monkeypatch, financeiro, resposta="O CDI e ...", envio=None):
    """Monta a rota com IA simulada e captura o que foi enviado ao WhatsApp."""
    import httpx
    from app.main import cliente_evolution

    monkeypatch.setattr(authorization, "ALLOWED_PHONE", "5511999999999")
    monkeypatch.setattr(authorization, "ALLOWED_LID", "")
    monkeypatch.setattr(authorization, "ALLOWED_GROUP_ID", "")
    app.dependency_overrides[get_state_store] = lambda: InMemoryBotState(True)

    async def fake_classify(client, texto):
        return financeiro

    async def fake_answer(client, texto):
        return resposta

    monkeypatch.setattr(main, "classify_financial_topic", fake_classify)
    monkeypatch.setattr(main, "answer_financial_question", fake_answer)

    enviados = envio if envio is not None else []

    def h(request):
        import json as _j
        enviados.append(_j.loads(request.read()))
        return httpx.Response(201, json={"key": {"id": "X"}})

    async def override():
        async with httpx.AsyncClient(
            base_url="https://evo.exemplo", transport=httpx.MockTransport(h)
        ) as c:
            yield c

    app.dependency_overrides[cliente_evolution] = override
    return TestClient(app, raise_server_exceptions=False), enviados


def test_pergunta_financeira_recebe_resposta_da_ia(monkeypatch):
    import app.main as main
    c, enviados = cliente_com_ia(monkeypatch, financeiro=True, resposta="O CDI e a taxa.")

    r = c.post("/webhook", json=msg("O que e CDI?"), headers=HEADERS)

    assert r.json()["processed"] is True
    assert enviados[0]["text"] == "O CDI e a taxa."
    app.dependency_overrides.clear()


def test_assunto_fora_do_dominio_e_recusado(monkeypatch):
    import app.main as main
    c, enviados = cliente_com_ia(monkeypatch, financeiro=False)

    r = c.post("/webhook", json=msg("Quem foi campeao em 2025?"), headers=HEADERS)

    # A recusa acontece em background; o que importa e o que chega ao usuario.
    assert r.status_code == 200
    assert "apenas sobre finanças" in enviados[0]["text"].lower()
    app.dependency_overrides.clear()


def test_comando_nao_passa_pelo_guard_de_financas(monkeypatch):
    """/ativar nao e assunto financeiro e nao pode ser bloqueado por isso."""
    import app.main as main
    c, enviados = cliente_com_ia(monkeypatch, financeiro=False)

    r = c.post("/webhook", json=msg("/desativar"), headers=HEADERS)

    assert r.json()["reply"] == "Finbox desativado."
    app.dependency_overrides.clear()


def test_pergunta_financeira_responde_em_background(monkeypatch):
    """A Evolution recebe 200 na hora; a IA roda depois.

    Sem isto ela espera 10-15s pela IA, estoura o timeout e reenvia o
    evento -- o Finbox responderia duas vezes.
    """
    c, enviados = cliente_com_ia(monkeypatch, financeiro=True, resposta="resposta.")

    r = c.post("/webhook", json=msg("O que e CDI?"), headers=HEADERS)

    assert r.status_code == 200
    assert r.json()["reason"] == "processando"
    assert enviados[0]["text"] == "resposta."
    app.dependency_overrides.clear()


# --- camada 4: documentos -------------------------------------------------

def doc_msg(nome="fatura.pdf", mime="application/pdf", tamanho=1024):
    return {
        "event": "messages.upsert",
        "instance": "finbox",
        "data": {
            "key": {"remoteJid": AUTORIZADO, "fromMe": False, "id": "MSG1"},
            "message": {
                "documentMessage": {
                    "fileName": nome, "mimetype": mime,
                    "fileLength": str(tamanho),
                }
            },
            "messageType": "documentMessage",
        },
    }


def cliente_com_documento(monkeypatch, transacoes=None, erro_download=None):
    import httpx
    from app.main import cliente_evolution
    from app.analise import Transacao
    from decimal import Decimal

    monkeypatch.setattr(authorization, "ALLOWED_PHONE", "5511999999999")
    monkeypatch.setattr(authorization, "ALLOWED_LID", "")
    monkeypatch.setattr(authorization, "ALLOWED_GROUP_ID", "")
    app.dependency_overrides[get_state_store] = lambda: InMemoryBotState(True)

    async def fake_baixar(client, message_id):
        if erro_download:
            raise erro_download
        return b"%PDF-fake"

    def fake_extrair(dados):
        return "texto extraido"

    async def fake_analisar(client, texto):
        return transacoes if transacoes is not None else []

    monkeypatch.setattr(main, "baixar_midia", fake_baixar)
    monkeypatch.setattr(main, "extrair_texto_pdf", fake_extrair)
    monkeypatch.setattr(main, "analisar", fake_analisar)

    enviados = []

    def h(request):
        import json as _j
        enviados.append(_j.loads(request.read()))
        return httpx.Response(201, json={"key": {"id": "X"}})

    async def override():
        async with httpx.AsyncClient(
            base_url="https://evo.exemplo", transport=httpx.MockTransport(h)
        ) as c:
            yield c

    app.dependency_overrides[cliente_evolution] = override
    return TestClient(app, raise_server_exceptions=False), enviados


def test_pdf_recebe_resumo_com_total_calculado(monkeypatch):
    from app.analise import Transacao

    c, enviados = cliente_com_documento(monkeypatch, transacoes=[
        Transacao("2026-08-01", "Uber", 34.90, "Transporte"),
        Transacao("2026-08-02", "Spotify", 21.90, "Assinaturas"),
    ])

    r = c.post("/webhook", json=doc_msg(), headers=HEADERS)

    assert r.status_code == 200
    assert "56,80" in enviados[0]["text"]
    app.dependency_overrides.clear()


def test_documento_que_nao_e_pdf_e_recusado_antes_de_baixar(monkeypatch):
    c, enviados = cliente_com_documento(monkeypatch)

    c.post("/webhook", json=doc_msg(nome="foto.jpg", mime="image/jpeg"),
           headers=HEADERS)

    assert "apenas PDF" in enviados[0]["text"]
    app.dependency_overrides.clear()


def test_arquivo_grande_demais_e_recusado(monkeypatch):
    from app.documento import LIMITE_BYTES

    c, enviados = cliente_com_documento(monkeypatch)

    c.post("/webhook", json=doc_msg(tamanho=LIMITE_BYTES + 1), headers=HEADERS)

    assert "grande demais" in enviados[0]["text"].lower()
    app.dependency_overrides.clear()


def test_falha_ao_baixar_avisa_o_usuario(monkeypatch):
    from app.adapters.evolution_adapter import EvolutionError

    c, enviados = cliente_com_documento(
        monkeypatch, erro_download=EvolutionError("Message not found")
    )

    c.post("/webhook", json=doc_msg(), headers=HEADERS)

    assert enviados, "o usuario precisa saber que falhou"
    app.dependency_overrides.clear()


def test_documento_nao_passa_pelo_guard_de_financas(monkeypatch):
    """Uma fatura ja e financeira por definicao; classificar seria gasto a toa."""
    from app.analise import Transacao

    chamou_guard = []

    async def guard_espiao(client, texto):
        chamou_guard.append(texto)
        return False

    monkeypatch.setattr(main, "classify_financial_topic", guard_espiao)
    c, enviados = cliente_com_documento(monkeypatch, transacoes=[
        Transacao("2026-08-01", "Uber", 10.00, "Transporte"),
    ])

    c.post("/webhook", json=doc_msg(), headers=HEADERS)

    assert chamou_guard == []
    app.dependency_overrides.clear()
