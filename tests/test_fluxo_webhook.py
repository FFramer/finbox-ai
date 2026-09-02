"""Ordem dos guards e efeito dos comandos na rota /webhook."""

import pytest
from fastapi.testclient import TestClient

from app import authorization, main
from app.parser import parse_event
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


# --- camada 3: resposta da IA ---------------------------------------------

def cliente_com_ia(monkeypatch, resposta="O CDI e ...", envio=None):
    """Monta a rota com IA simulada e captura o que foi enviado ao WhatsApp."""
    import httpx
    from app.main import cliente_evolution

    monkeypatch.setattr(authorization, "ALLOWED_PHONE", "5511999999999")
    monkeypatch.setattr(authorization, "ALLOWED_LID", "")
    monkeypatch.setattr(authorization, "ALLOWED_GROUP_ID", "")
    app.dependency_overrides[get_state_store] = lambda: InMemoryBotState(True)

    chamadas = []

    async def fake_answer(client, conversa, resumo=None, dados=None):
        chamadas.append(conversa)
        return resposta

    monkeypatch.setattr(main, "answer_financial_question", fake_answer)
    monkeypatch.setattr(main, "_chamadas_de_resposta", chamadas, raising=False)

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
    c, enviados = cliente_com_ia(monkeypatch, resposta="O CDI e a taxa.")

    r = c.post("/webhook", json=msg("O que e CDI?"), headers=HEADERS)

    assert r.json()["processed"] is True
    assert enviados[0]["text"] == "O CDI e a taxa."
    app.dependency_overrides.clear()


def test_comando_nao_chama_o_modelo(monkeypatch):
    """/ativar e deterministico: nao passa pela IA nem depende dela."""
    c, enviados = cliente_com_ia(monkeypatch)

    r = c.post("/webhook", json=msg("/desativar"), headers=HEADERS)

    assert r.json()["reply"] == "Finbox desativado."
    assert main._chamadas_de_resposta == []
    app.dependency_overrides.clear()


def test_pergunta_financeira_responde_em_background(monkeypatch):
    """A Evolution recebe 200 na hora; a IA roda depois.

    Sem isto ela espera 10-15s pela IA, estoura o timeout e reenvia o
    evento -- o Finbox responderia duas vezes.
    """
    c, enviados = cliente_com_ia(monkeypatch, resposta="resposta.")

    r = c.post("/webhook", json=msg("O que e CDI?"), headers=HEADERS)

    assert r.status_code == 200
    assert r.json()["reason"] == "processando"
    assert enviados[0]["text"] == "resposta."
    app.dependency_overrides.clear()


# --- camada 4: documentos -------------------------------------------------

def doc_msg(nome="fatura.pdf", mime="application/pdf", tamanho=1024,
            caption=None):
    documento = {
        "fileName": nome, "mimetype": mime,
        # A v2 manda o Long do protobuf; a string cobre a outra forma.
        "fileLength": str(tamanho),
    }

    if caption is not None:
        documento["caption"] = caption

    return {
        "event": "messages.upsert",
        "instance": "finbox",
        "data": {
            "key": {"remoteJid": AUTORIZADO, "fromMe": False, "id": "MSG1"},
            "message": {"documentMessage": documento},
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


def test_documento_nao_passa_pelo_fluxo_de_conversa(monkeypatch):
    """Fatura tem caminho proprio: extrai e resume, sem passar pela conversa."""
    from app.analise import Transacao

    chamou_guard = []

    async def resposta_espia(client, conversa, resumo=None, dados=None):
        chamou_guard.append(conversa)
        return "nao deveria"

    monkeypatch.setattr(main, "answer_financial_question", resposta_espia)
    c, enviados = cliente_com_documento(monkeypatch, transacoes=[
        Transacao("2026-08-01", "Uber", 10.00, "Transporte"),
    ])

    c.post("/webhook", json=doc_msg(), headers=HEADERS)

    assert chamou_guard == []
    app.dependency_overrides.clear()


def test_caption_do_pdf_e_respondido_junto_com_o_resumo(monkeypatch):
    """A legenda e um pedido; o resumo fixo sozinho nao responde a ela."""
    from app.analise import Transacao

    c, enviados = cliente_com_documento(monkeypatch, transacoes=[
        Transacao("2026-08-01", "Uber", 34.90, "Transporte"),
        Transacao("2026-08-02", "Spotify", 21.90, "Assinaturas"),
    ])

    async def fake_responder(client, resultado, transacoes, pergunta):
        return f"Sobre '{pergunta}': o Uber foi o maior gasto."

    monkeypatch.setattr(main, "responder_sobre", fake_responder, raising=False)

    c.post("/webhook", json=doc_msg(caption="qual foi o maior gasto?"),
           headers=HEADERS)

    texto = enviados[0]["text"]

    assert "56,80" in texto, "o resumo determinístico continua saindo"
    assert "o Uber foi o maior gasto" in texto
    app.dependency_overrides.clear()


def test_falha_na_pergunta_extra_nao_custa_o_resumo(monkeypatch):
    """Um extra que falha nunca pode derrubar a analise que ja deu certo."""
    from app.ai import AIError
    from app.analise import Transacao

    c, enviados = cliente_com_documento(monkeypatch, transacoes=[
        Transacao("2026-08-01", "Uber", 34.90, "Transporte"),
        Transacao("2026-08-02", "Spotify", 21.90, "Assinaturas"),
    ])

    async def fake_responder(client, resultado, transacoes, pergunta):
        raise AIError("provedor fora do ar")

    monkeypatch.setattr(main, "responder_sobre", fake_responder, raising=False)

    c.post("/webhook", json=doc_msg(caption="qual foi o maior gasto?"),
           headers=HEADERS)

    assert enviados, "o resumo precisa chegar mesmo assim"
    assert "56,80" in enviados[0]["text"]
    app.dependency_overrides.clear()


def test_pdf_sem_caption_nao_gasta_chamada_extra(monkeypatch):
    from app.analise import Transacao

    chamadas = []

    c, enviados = cliente_com_documento(monkeypatch, transacoes=[
        Transacao("2026-08-01", "Uber", 34.90, "Transporte"),
    ])

    async def fake_responder(client, resultado, transacoes, pergunta):
        chamadas.append(pergunta)
        return "nao deveria ter sido chamado"

    monkeypatch.setattr(main, "responder_sobre", fake_responder, raising=False)

    c.post("/webhook", json=doc_msg(), headers=HEADERS)

    assert chamadas == []
    app.dependency_overrides.clear()


# --- fase 1: lancamentos vao para o banco ---------------------------------

def _duas_transacoes():
    from app.analise import Transacao

    return [
        Transacao("2026-08-01", "Uber", 34.90, "Transporte"),
        Transacao("2026-08-02", "Spotify", 21.90, "Assinaturas"),
    ]


def test_lancamentos_do_pdf_sao_persistidos(monkeypatch):
    """Sem isto os lancamentos morrem junto com a background task."""
    from app.history import InMemoryHistory

    history = InMemoryHistory()
    app.dependency_overrides[main.get_history_store] = lambda: history

    c, enviados = cliente_com_documento(monkeypatch, transacoes=_duas_transacoes())
    c.post("/webhook", json=doc_msg(), headers=HEADERS)

    [(_, linhas)] = history.transactions.items()

    assert [linha["description"] for linha in linhas] == ["Uber", "Spotify"]
    assert [linha["amount"] for linha in linhas] == ["34.90", "21.90"]
    app.dependency_overrides.clear()


def test_falha_ao_gravar_lancamentos_nao_custa_a_resposta(monkeypatch):
    """O usuario ja esperou a analise; perde-la por causa do banco seria pior."""
    from app.history import HistoryError, InMemoryHistory

    class BancoQueFalha(InMemoryHistory):
        async def record_transactions(self, ref, transacoes):
            raise HistoryError("supabase fora do ar")

    app.dependency_overrides[main.get_history_store] = lambda: BancoQueFalha()

    c, enviados = cliente_com_documento(monkeypatch, transacoes=_duas_transacoes())
    c.post("/webhook", json=doc_msg(), headers=HEADERS)

    assert enviados, "o resumo precisa chegar mesmo assim"
    assert "56,80" in enviados[0]["text"]
    app.dependency_overrides.clear()


# --- fase 0: uma chamada por mensagem -------------------------------------

def _cliente_de_texto(monkeypatch, resposta="resposta.", erro=None):
    import httpx
    from app.main import cliente_evolution

    monkeypatch.setattr(authorization, "ALLOWED_PHONE", "5511999999999")
    monkeypatch.setattr(authorization, "ALLOWED_LID", "")
    monkeypatch.setattr(authorization, "ALLOWED_GROUP_ID", "")
    app.dependency_overrides[get_state_store] = lambda: InMemoryBotState(True)

    chamadas = []
    main._dados_vistos = []

    async def responder(ia, conversa, resumo=None, dados=None):
        chamadas.append([(m.role, m.content) for m in conversa])
        main._dados_vistos.append(dados)
        if erro is not None:
            raise erro
        return resposta

    monkeypatch.setattr(main, "answer_financial_question", responder)

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
    return TestClient(app, raise_server_exceptions=False), enviados, chamadas


def test_mensagem_de_texto_faz_exatamente_uma_chamada_de_resposta(monkeypatch):
    """Duas chamadas significam que o classificador voltou com outro nome."""
    c, enviados, chamadas = _cliente_de_texto(monkeypatch)

    c.post("/webhook", json=msg("quanto gastei?"), headers=HEADERS)

    assert len(chamadas) == 1
    assert not hasattr(main, "classify_financial_topic")
    assert enviados[0]["text"] == "resposta."
    app.dependency_overrides.clear()


def test_continuacao_curta_nao_e_bloqueada(monkeypatch):
    """'sim, quero ver' era recusada pelo guard; agora chega ao modelo."""
    c, enviados, chamadas = _cliente_de_texto(monkeypatch, resposta="Claro:")

    c.post("/webhook", json=msg("sim, quero ver"), headers=HEADERS)

    assert chamadas[-1][-1] == ("user", "sim, quero ver")
    assert enviados[0]["text"] == "Claro:"
    app.dependency_overrides.clear()


def test_falha_do_provedor_vira_indisponibilidade_e_nunca_recusa(monkeypatch):
    from app.ai import AIError

    c, enviados, _ = _cliente_de_texto(monkeypatch, erro=AIError("429 cota"))

    c.post("/webhook", json=msg("quanto gastei?"), headers=HEADERS)

    assert enviados[0]["text"] == main.INDISPONIVEL
    app.dependency_overrides.clear()


def test_recusa_fixa_de_dominio_nao_existe_mais_no_fluxo(monkeypatch):
    assert not hasattr(main, "FORA_DO_DOMINIO")




# --- fases 2 e 3: lancamentos e comparacao no contexto --------------------

def test_lancamentos_gravados_chegam_ao_modelo(monkeypatch):
    """Sem o bloco, 'quanto gastei de Uber' nao tem como ser respondido."""
    import asyncio

    from app.analise import Transacao
    from app.history import InMemoryHistory
    from app.parser import Documento, ParsedEvent

    history = InMemoryHistory()

    async def semear():
        base = parse_event(msg("fatura"))
        doc = ParsedEvent(
            **{
                **base.__dict__,
                "message_id": "DOC-1",
                "text": None,
                "documento": Documento("fatura.pdf", "application/pdf", 100),
            }
        )
        inbound = await history.record_inbound(doc)
        await history.mark_inbound(inbound, "completed")
        await history.record_transactions(inbound, [
            Transacao("2026-07-05", "POSTO IPIRANGA", 200.00, "Transporte"),
            Transacao("2026-08-03", "UBER *TRIP", 27.90, "Transporte"),
        ])

    asyncio.run(semear())

    c, enviados, _ = _cliente_de_texto(monkeypatch)
    app.dependency_overrides[main.get_history_store] = lambda: history

    c.post("/webhook", json=msg("quanto gastei de uber?"), headers=HEADERS)

    bloco = main._dados_vistos[-1]

    assert bloco is not None, "o bloco de lancamentos precisa chegar ao modelo"
    assert "UBER *TRIP" in bloco
    assert "R$ 27,90" in bloco
    # fase 3: totais por mes, somados em Python
    assert "2026-07" in bloco and "2026-08" in bloco
    assert "R$ 200,00" in bloco
    app.dependency_overrides.clear()
