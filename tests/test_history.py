from datetime import datetime, timedelta, timezone

import pytest

from app.history import InMemoryHistory
from app.parser import Documento, ParsedEvent


def event(message_id="MSG-1", chat_id="5511999999999@s.whatsapp.net"):
    return ParsedEvent(
        chat_id=chat_id,
        from_me=False,
        is_group=False,
        text="Quanto gastei?",
        message_type="conversation",
        author_id="5511999999999@s.whatsapp.net",
        push_name="Fabio",
        message_id=message_id,
        instance="finbox",
        occurred_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_registro_inbound_cria_principal_identidade_e_conversa():
    history = InMemoryHistory()

    ref = await history.record_inbound(event())
    messages = await history.recent_messages(ref.conversation_id)

    assert ref.created is True
    assert len(history.principals) == 1
    assert len(history.identities) == 1
    assert len(history.conversations) == 1
    assert messages[0].content == "Quanto gastei?"
    assert messages[0].role == "user"


@pytest.mark.asyncio
async def test_message_id_repetido_na_mesma_conversa_e_idempotente():
    history = InMemoryHistory()

    first = await history.record_inbound(event())
    duplicate = await history.record_inbound(event())

    assert duplicate.created is False
    assert duplicate.message_id == first.message_id
    assert len(history.messages) == 1


@pytest.mark.asyncio
async def test_mesmo_message_id_em_conversas_distintas_nao_colide():
    history = InMemoryHistory()

    first = await history.record_inbound(event(chat_id="grupo-1@g.us"))
    second = await history.record_inbound(event(chat_id="grupo-2@g.us"))

    assert first.created is True
    assert second.created is True
    assert first.conversation_id != second.conversation_id


@pytest.mark.asyncio
async def test_registra_resposta_e_estado_de_processamento():
    history = InMemoryHistory()
    inbound = await history.record_inbound(event())

    await history.mark_inbound(inbound, "completed")
    await history.record_outbound(
        inbound,
        "Voce gastou R$ 10,00.",
        provider_message_id="OUT-1",
        delivered=True,
    )
    messages = await history.recent_messages(inbound.conversation_id)

    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].processing_status == "completed"
    assert messages[1].delivery_status == "sent"
    assert messages[1].reply_to_message_id == inbound.message_id


@pytest.mark.asyncio
async def test_documento_guarda_apenas_metadados_estruturados():
    history = InMemoryHistory()
    incoming = event()
    incoming = ParsedEvent(
        **{
            **incoming.__dict__,
            "text": None,
            "documento": Documento("fatura.pdf", "application/pdf", 1234),
        }
    )

    ref = await history.record_inbound(incoming)
    [message] = await history.recent_messages(ref.conversation_id)

    assert message.kind == "document"
    assert message.metadata["document"]["name"] == "fatura.pdf"


def comando(message_id="CMD-1"):
    return ParsedEvent(
        **{**event(message_id=message_id).__dict__, "text": "/ativar"}
    )


async def conversa_variada():
    """Monta uma conversa com tudo o que a janela precisa descartar."""
    history = InMemoryHistory()

    cmd = await history.record_inbound(comando())
    await history.mark_inbound(cmd, "completed")

    ignorada = await history.record_inbound(event(message_id="MSG-IGN"))
    await history.mark_inbound(ignorada, "ignored", "disabled")

    valida = await history.record_inbound(event(message_id="MSG-OK"))
    await history.mark_inbound(valida, "completed")
    await history.record_outbound(
        valida, "Nao chegou", provider_message_id=None, delivered=False
    )
    await history.record_outbound(
        valida, "Chegou", provider_message_id="OUT-OK", delivered=True
    )

    atual = await history.record_inbound(event(message_id="MSG-ATUAL"))
    await history.mark_inbound(atual, "processing")

    return history, valida, atual


@pytest.mark.asyncio
async def test_janela_descarta_comando_ignorada_e_resposta_nao_entregue():
    history, _, atual = await conversa_variada()

    janela = await history.recent_eligible(
        atual.conversation_id, up_to_id=atual.message_id, limit=20
    )

    assert [(m.role, m.content) for m in janela] == [
        ("user", "Quanto gastei?"),
        ("assistant", "Chegou"),
        ("user", "Quanto gastei?"),
    ]


@pytest.mark.asyncio
async def test_janela_nao_enxerga_mensagens_posteriores_a_atual():
    """Duas mensagens processadas em paralelo nao podem se ver fora de ordem."""
    history, valida, atual = await conversa_variada()
    depois = await history.record_inbound(event(message_id="MSG-DEPOIS"))
    await history.mark_inbound(depois, "processing")

    janela = await history.recent_eligible(
        atual.conversation_id, up_to_id=atual.message_id, limit=20
    )

    assert all(int(m.id) <= int(atual.message_id) for m in janela)


@pytest.mark.asyncio
async def test_janela_respeita_o_teto_mantendo_as_mais_recentes():
    history, _, atual = await conversa_variada()

    janela = await history.recent_eligible(
        atual.conversation_id, up_to_id=atual.message_id, limit=2
    )

    assert [m.content for m in janela] == ["Chegou", "Quanto gastei?"]


@pytest.mark.asyncio
async def test_elegiveis_apos_o_watermark_vem_das_mais_antigas():
    history, valida, atual = await conversa_variada()

    posteriores = await history.eligible_after(
        atual.conversation_id, after_id=valida.message_id, limit=10
    )

    assert [m.content for m in posteriores] == ["Chegou", "Quanto gastei?"]


@pytest.mark.asyncio
async def test_resumo_ausente_ate_ser_gravado():
    history, _, atual = await conversa_variada()

    assert await history.conversation_summary(atual.conversation_id) is None

    aplicado = await history.save_summary(
        atual.conversation_id,
        summary="Conversa sobre gastos.",
        covers_through_message_id=atual.message_id,
        covered_message_count=3,
        model="modelo/resumo",
        prompt_version="v1",
        expected_previous_message_id=None,
    )
    resumo = await history.conversation_summary(atual.conversation_id)

    assert aplicado is True
    assert resumo.summary == "Conversa sobre gastos."
    assert resumo.covers_through_message_id == atual.message_id


@pytest.mark.asyncio
async def test_resumo_concorrente_nao_faz_o_watermark_retroceder():
    """Duas mensagens da mesma conversa podem resumir ao mesmo tempo."""
    history, valida, atual = await conversa_variada()
    await history.save_summary(
        atual.conversation_id,
        summary="Ja avancou.",
        covers_through_message_id=atual.message_id,
        covered_message_count=3,
        model="modelo/resumo",
        prompt_version="v1",
        expected_previous_message_id=None,
    )

    perdedor = await history.save_summary(
        atual.conversation_id,
        summary="Chegou tarde.",
        covers_through_message_id=valida.message_id,
        covered_message_count=2,
        model="modelo/resumo",
        prompt_version="v1",
        expected_previous_message_id=None,
    )
    resumo = await history.conversation_summary(atual.conversation_id)

    assert perdedor is False
    assert resumo.summary == "Ja avancou."


@pytest.mark.asyncio
async def test_mensagens_orfas_em_processing_viram_falhas():
    """Um restart mata as background tasks em voo. Sem varredura elas ficam
    em processing para sempre -- e desde a fase 2 continuam elegiveis para
    o contexto, aparecendo em toda janela futura da conversa."""
    history = InMemoryHistory()
    presa = await history.record_inbound(event(message_id="MSG-PRESA"))
    await history.mark_inbound(presa, "processing")
    pronta = await history.record_inbound(event(message_id="MSG-PRONTA"))
    await history.mark_inbound(pronta, "completed")
    nova = await history.record_inbound(event(message_id="MSG-NOVA"))

    reaped = await history.fail_stuck_processing(
        datetime.now(timezone.utc) + timedelta(minutes=1)
    )

    assert reaped == 1
    assert history.messages[int(presa.message_id)].processing_status == "failed"
    assert history.messages[int(presa.message_id)].ignored_reason == "orphaned"
    assert history.messages[int(pronta.message_id)].processing_status == "completed"
    assert history.messages[int(nova.message_id)].processing_status == "received"


@pytest.mark.asyncio
async def test_varredura_nao_toca_em_mensagem_recente():
    """Com mais de uma replica, uma subida nao pode matar o trabalho em voo
    da outra -- por isso a varredura tem corte por idade."""
    history = InMemoryHistory()
    recente = await history.record_inbound(event(message_id="MSG-RECENTE"))
    await history.mark_inbound(recente, "processing")

    reaped = await history.fail_stuck_processing(
        datetime.now(timezone.utc) - timedelta(minutes=1)
    )

    assert reaped == 0
    assert history.messages[int(recente.message_id)].processing_status == "processing"


# --- fase 1: persistencia dos lancamentos ---------------------------------

def documento_event(message_id="MSG-DOC"):
    return ParsedEvent(
        **{
            **event(message_id=message_id).__dict__,
            "text": None,
            "documento": Documento("fatura.pdf", "application/pdf", 26110),
        }
    )


def _transacoes():
    from app.analise import Transacao

    return [
        Transacao("2026-08-02", "SUPERMERCADO ZONA SUL", 186.42, "Mercado"),
        Transacao("2026-08-03", "UBER *TRIP", 27.90, "Transporte"),
    ]


def test_mapeia_transacoes_para_linhas_do_banco():
    from app.history import transaction_rows

    linhas = transaction_rows(_transacoes())

    assert linhas[0]["occurred_on"] == "2026-08-02"
    assert linhas[0]["description"] == "SUPERMERCADO ZONA SUL"
    assert linhas[0]["amount"] == "186.42"
    assert linhas[0]["category"] == "Mercado"
    assert [linha["position"] for linha in linhas] == [1, 2]


def test_data_invalida_do_modelo_vira_nulo_em_vez_de_quebrar():
    """O modelo devolve '02 AGO' ou vazio quando nao entende o layout."""
    from app.analise import Transacao
    from app.history import transaction_rows

    linhas = transaction_rows([
        Transacao("02 AGO", "PADARIA REAL", 28.50, "Alimentacao"),
        Transacao("", "CAFE DO CENTRO", 24.00, "Alimentacao"),
    ])

    assert linhas[0]["occurred_on"] is None
    assert linhas[1]["occurred_on"] is None
    assert linhas[0]["description"] == "PADARIA REAL"


@pytest.mark.asyncio
async def test_grava_os_lancamentos_do_documento():
    history = InMemoryHistory()
    inbound = await history.record_inbound(documento_event())

    total = await history.record_transactions(inbound, _transacoes())

    assert total == 2
    guardadas = history.transactions_for(inbound.message_id)
    assert [t["description"] for t in guardadas] == [
        "SUPERMERCADO ZONA SUL", "UBER *TRIP"
    ]


@pytest.mark.asyncio
async def test_reprocessar_o_mesmo_documento_nao_duplica():
    """Reenvio do mesmo evento nao pode dobrar a fatura no banco."""
    history = InMemoryHistory()
    inbound = await history.record_inbound(documento_event())

    await history.record_transactions(inbound, _transacoes())
    await history.record_transactions(inbound, _transacoes())

    assert len(history.transactions_for(inbound.message_id)) == 2
