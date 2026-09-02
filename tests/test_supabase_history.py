import json

from datetime import datetime, timezone

import httpx
import pytest

from app.adapters.supabase_history_adapter import SupabaseHistory
from app.history import HistoryError, MessageRef
from app.parser import ParsedEvent


def event():
    return ParsedEvent(
        chat_id="5511999999999@s.whatsapp.net",
        from_me=False,
        is_group=False,
        text="O que e CDI?",
        message_type="conversation",
        author_id="5511999999999@s.whatsapp.net",
        push_name="Fabio",
        message_id="MSG-1",
        instance="finbox",
    )


@pytest.mark.asyncio
async def test_inbound_usa_rpc_atomica_e_preserva_idempotencia():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.read())
        return httpx.Response(
            200,
            json=[{"conversation_id": 10, "message_id": 20, "created": False}],
        )

    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        ref = await SupabaseHistory(client).record_inbound(event())

    assert captured["path"].endswith("/rpc/record_inbound_message")
    assert captured["body"]["p_provider_message_id"] == "MSG-1"
    assert captured["body"]["p_identity_type"] == "phone"
    assert ref.created is False


@pytest.mark.asyncio
async def test_outbound_fica_ligado_a_mensagem_que_originou_a_resposta():
    requests = []

    def handler(request):
        requests.append(json.loads(request.read()))
        return httpx.Response(
            200,
            json=[{"conversation_id": 10, "message_id": 21, "created": True}],
        )

    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        history = SupabaseHistory(client)
        inbound = type("Ref", (), {
            "conversation_id": 10, "message_id": 20, "created": True
        })()
        await history.record_outbound(
            inbound,
            "O CDI e...",
            provider_message_id="OUT-1",
            delivered=True,
        )

    assert requests[0]["p_reply_to_message_id"] == 20
    assert requests[0]["p_delivery_status"] == "sent"


@pytest.mark.asyncio
async def test_marcar_mensagem_inexistente_e_falha_e_nao_sucesso_silencioso():
    """PostgREST devolve 204/lista vazia quando o filtro nao casa nenhuma linha.

    Sem esta checagem o adapter daria a atualizacao por feita, enquanto o
    InMemoryHistory levanta HistoryError no mesmo caso.
    """

    def handler(request):
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(HistoryError):
            await SupabaseHistory(client).mark_inbound(
                MessageRef(10, 999, True), "completed"
            )


@pytest.mark.asyncio
async def test_marcar_mensagem_envia_status_e_motivo_da_linha_certa():
    captured = {}

    def handler(request):
        captured["params"] = dict(request.url.params)
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json=[{"id": 20}])

    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        await SupabaseHistory(client).mark_inbound(
            MessageRef(10, 20, True), "ignored", "disabled"
        )

    assert captured["params"]["id"] == "eq.20"
    assert captured["body"] == {
        "processing_status": "ignored",
        "ignored_reason": "disabled",
    }


@pytest.mark.asyncio
async def test_historico_recente_volta_em_ordem_cronologica():
    """O PostgREST ordena decrescente para o limit pegar as mais novas; quem
    monta contexto para o modelo precisa da ordem da conversa."""

    def handler(request):
        return httpx.Response(
            200,
            json=[
                {
                    "id": 21,
                    "conversation_id": 10,
                    "direction": "outbound",
                    "role": "assistant",
                    "kind": "text",
                    "content": "O CDI e...",
                    "processing_status": "completed",
                    "delivery_status": "sent",
                    "provider_message_id": "OUT-1",
                    "reply_to_message_id": 20,
                    "occurred_at": "2026-08-31T12:00:01Z",
                    "metadata": None,
                    "ignored_reason": None,
                },
                {
                    "id": 20,
                    "conversation_id": 10,
                    "direction": "inbound",
                    "role": "user",
                    "kind": "text",
                    "content": "O que e CDI?",
                    "processing_status": "completed",
                    "delivery_status": None,
                    "provider_message_id": "MSG-1",
                    "reply_to_message_id": None,
                    "occurred_at": "2026-08-31T12:00:00Z",
                    "metadata": {"author_identity_id": 3},
                    "ignored_reason": None,
                },
            ],
        )

    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        messages = await SupabaseHistory(client).recent_messages(10, limit=2)

    assert [message.id for message in messages] == [20, 21]
    assert messages[0].occurred_at.year == 2026
    assert messages[0].occurred_at.tzinfo is not None
    assert messages[0].metadata == {"author_identity_id": 3}


@pytest.mark.asyncio
async def test_historico_recente_nao_chama_o_banco_sem_limite_util():
    def handler(request):
        raise AssertionError("nao deveria consultar o Supabase")

    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert await SupabaseHistory(client).recent_messages(10, limit=0) == []


@pytest.mark.asyncio
async def test_erro_do_supabase_vira_history_error():
    def handler(request):
        return httpx.Response(500, text="internal error")

    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(HistoryError):
            await SupabaseHistory(client).record_inbound(event())


@pytest.mark.asyncio
async def test_supabase_fora_do_ar_vira_history_error():
    """O webhook so devolve 503 se a falha de rede chegar como HistoryError."""

    def handler(request):
        raise httpx.ConnectError("connection refused")

    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(HistoryError):
            await SupabaseHistory(client).record_inbound(event())


# --- fase 2: janela elegivel e resumo ------------------------------------

def linha(id_, direction="inbound", role="user", content="oi", **extra):
    base = {
        "id": id_,
        "conversation_id": 10,
        "direction": direction,
        "role": role,
        "kind": "text",
        "content": content,
        "processing_status": "completed",
        "delivery_status": "sent" if direction == "outbound" else None,
        "provider_message_id": f"P-{id_}",
        "reply_to_message_id": None,
        "occurred_at": f"2026-08-31T12:00:0{id_}Z",
        "metadata": None,
        "ignored_reason": None,
    }
    return {**base, **extra}


def capturar(corpo):
    capturado = {}

    def handler(request):
        capturado["path"] = request.url.path
        capturado["params"] = request.url.params.multi_items()
        capturado["body"] = request.read().decode() or None
        return httpx.Response(200, json=corpo)

    return handler, capturado


async def com_adapter(handler, acao):
    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        return await acao(SupabaseHistory(client))


@pytest.mark.asyncio
async def test_a_janela_e_filtrada_no_banco_antes_do_limit():
    """Filtrar depois do LIMIT deixaria mensagens invalidas ocupando a
    janela e empurrando as uteis para fora."""
    handler, capturado = capturar([])

    await com_adapter(
        handler,
        lambda h: h.recent_eligible(10, up_to_id=99, limit=20),
    )

    params = dict(capturado["params"])
    assert params["order"] == "id.desc"
    assert params["limit"] == "20"
    assert params["id"] == "lte.99"
    assert params["role"] == "in.(user,assistant)"
    assert params["kind"] == "neq.command"
    assert "direction.eq.inbound" in params["or"]
    assert "delivery_status.eq.sent" in params["or"]


@pytest.mark.asyncio
async def test_a_janela_volta_em_ordem_cronologica():
    handler, _ = capturar([linha(3, "outbound", "assistant", "resposta"), linha(2)])

    janela = await com_adapter(
        handler, lambda h: h.recent_eligible(10, up_to_id=3, limit=20)
    )

    assert [m.id for m in janela] == [2, 3]


@pytest.mark.asyncio
async def test_elegiveis_apos_o_watermark_pedem_ordem_crescente():
    handler, capturado = capturar([linha(4), linha(5)])

    posteriores = await com_adapter(
        handler, lambda h: h.eligible_after(10, after_id=3, limit=21)
    )

    params = dict(capturado["params"])
    assert params["order"] == "id.asc"
    assert params["id"] == "gt.3"
    assert [m.id for m in posteriores] == [4, 5]


@pytest.mark.asyncio
async def test_conversa_sem_resumo_devolve_none():
    handler, _ = capturar([])

    assert await com_adapter(handler, lambda h: h.conversation_summary(10)) is None


@pytest.mark.asyncio
async def test_resumo_existente_vira_dataclass():
    handler, _ = capturar([{
        "conversation_id": 10,
        "summary": "Falou de fatura.",
        "covers_through_message_id": 7,
        "covered_message_count": 4,
        "model": "modelo/x",
        "prompt_version": "resumo-v1",
    }])

    resumo = await com_adapter(handler, lambda h: h.conversation_summary(10))

    assert resumo.summary == "Falou de fatura."
    assert resumo.covers_through_message_id == 7
    assert resumo.covered_message_count == 4


@pytest.mark.asyncio
async def test_save_summary_repassa_a_decisao_da_rpc():
    handler, capturado = capturar(False)

    aplicado = await com_adapter(handler, lambda h: h.save_summary(
        10,
        summary="novo",
        covers_through_message_id=9,
        covered_message_count=5,
        model="modelo/x",
        prompt_version="resumo-v1",
        expected_previous_message_id=7,
    ))

    assert capturado["path"].endswith("/rpc/save_conversation_summary")
    assert aplicado is False
    assert json.loads(capturado["body"])["p_expected_previous_message_id"] == 7


@pytest.mark.asyncio
async def test_varredura_de_orfas_usa_o_indice_de_pendentes():
    corte = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    handler, capturado = capturar([{"id": 4}, {"id": 7}])

    reaped = await com_adapter(
        handler, lambda h: h.fail_stuck_processing(corte)
    )

    params = dict(capturado["params"])
    assert capturado["path"].endswith("/messages")
    assert params["processing_status"] == "eq.processing"
    assert params["created_at"] == f"lt.{corte.isoformat()}"
    assert json.loads(capturado["body"]) == {
        "processing_status": "failed",
        "ignored_reason": "orphaned",
    }
    assert reaped == 2


@pytest.mark.asyncio
async def test_lancamentos_vao_para_a_rpc_com_as_linhas_prontas():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json=2)

    from app.analise import Transacao

    transacoes = [
        Transacao("2026-08-02", "SUPERMERCADO ZONA SUL", 186.42, "Mercado"),
        Transacao("02 AGO", "PADARIA REAL", 28.50, "Alimentacao"),
    ]

    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        total = await SupabaseHistory(client).record_transactions(
            MessageRef(conversation_id=10, message_id=20, created=True),
            transacoes,
        )

    assert captured["path"].endswith("/rpc/record_document_transactions")
    assert captured["body"]["p_message_id"] == 20
    enviadas = captured["body"]["p_transactions"]
    assert enviadas[0]["amount"] == "186.42"
    assert enviadas[0]["occurred_on"] == "2026-08-02"
    # data que o modelo inventou nao pode virar erro de cast no Postgres
    assert enviadas[1]["occurred_on"] is None
    assert total == 2


@pytest.mark.asyncio
async def test_falha_do_supabase_ao_gravar_lancamentos_vira_history_error():
    def handler(request):
        return httpx.Response(500, text="boom")

    from app.analise import Transacao

    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(HistoryError):
            await SupabaseHistory(client).record_transactions(
                MessageRef(conversation_id=10, message_id=20, created=True),
                [Transacao("2026-08-02", "UBER", 27.90, "Transporte")],
            )


# --- fase 0: recusa legada excluida no banco, antes do LIMIT --------------

def _captura_params():
    capturado = {}

    def handler(request):
        capturado["content"] = request.url.params.get_list("content")
        capturado["limit"] = request.url.params.get("limit")
        return httpx.Response(200, json=[])

    return capturado, httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_recent_eligible_exclui_a_recusa_legada_no_banco():
    """Filtrar depois do LIMIT deixaria recusas ocupando vaga da janela."""
    from app.history import RECUSAS_LEGADAS

    capturado, transport = _captura_params()
    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1", transport=transport
    ) as client:
        await SupabaseHistory(client).recent_eligible(9, up_to_id=70, limit=20)

    for recusa in RECUSAS_LEGADAS:
        assert f"neq.{recusa}" in capturado["content"], recusa
    assert capturado["limit"] == "20", "a janela util nao pode encolher"


@pytest.mark.asyncio
async def test_eligible_after_tambem_exclui_a_recusa_legada():
    """Sem isto a recusa entraria no proximo resumo rolling."""
    from app.history import RECUSAS_LEGADAS

    capturado, transport = _captura_params()
    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1", transport=transport
    ) as client:
        await SupabaseHistory(client).eligible_after(9, after_id=10, limit=21)

    for recusa in RECUSAS_LEGADAS:
        assert f"neq.{recusa}" in capturado["content"], recusa


@pytest.mark.asyncio
async def test_lancamentos_da_conversa_vem_do_banco_ordenados():
    capturado = {}

    def handler(request):
        capturado["path"] = request.url.path
        capturado["params"] = dict(request.url.params)
        return httpx.Response(200, json=[
            {"message_id": 67, "position": 2, "occurred_on": "2026-08-03",
             "description": "UBER *TRIP", "amount": "27.90",
             "category": "Transporte"},
            {"message_id": 67, "position": 1, "occurred_on": "2026-08-02",
             "description": "SUPERMERCADO", "amount": "186.42",
             "category": "Mercado"},
        ])

    async with httpx.AsyncClient(
        base_url="https://projeto.supabase.co/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        linhas = await SupabaseHistory(client).transactions_for_conversation(9)

    assert capturado["path"].endswith("/transactions")
    assert capturado["params"]["conversation_id"] == "eq.9"
    # desc + limit pega as mais recentes; a ordem util e cronologica.
    assert [l["position"] for l in linhas] == [1, 2]
