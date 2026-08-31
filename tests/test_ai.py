"""Camada 3: guard de dominio financeiro e resposta, via OpenRouter."""

import json

import httpx
import pytest

from app.ai import AIError, answer_financial_question, classify_financial_topic


def resposta_do_modelo(conteudo):
    def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": conteudo}}]},
        )

    return handler


async def com(handler, funcao, texto="O que e CDI?"):
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://openrouter.exemplo/api/v1", transport=transport
    ) as client:
        return await funcao(client, texto)


# --- classificador --------------------------------------------------------

async def test_reconhece_assunto_financeiro():
    h = resposta_do_modelo('{"is_financial": true}')

    assert await com(h, classify_financial_topic) is True


async def test_reconhece_assunto_fora_do_dominio():
    h = resposta_do_modelo('{"is_financial": false}')

    assert await com(h, classify_financial_topic) is False


@pytest.mark.parametrize(
    "conteudo",
    [
        '```json\n{"is_financial": true}\n```',
        'Claro! {"is_financial": true}',
        '{"is_financial":true,"motivo":"fala de CDI"}',
    ],
)
async def test_tolera_variacoes_de_formato_entre_modelos(conteudo):
    """Modelos diferentes embrulham o JSON de jeitos diferentes."""
    assert await com(resposta_do_modelo(conteudo), classify_financial_topic) is True


async def test_resposta_ilegivel_bloqueia_em_vez_de_liberar():
    """Um guard que falha aberto nao e um guard."""
    h = resposta_do_modelo("nao faco ideia do que voce quer")

    assert await com(h, classify_financial_topic) is False


async def test_falha_do_provedor_bloqueia():
    def h(request):
        return httpx.Response(500, json={"error": "indisponivel"})

    assert await com(h, classify_financial_topic) is False


async def test_erro_de_rede_bloqueia():
    def h(request):
        raise httpx.ConnectError("sem rede")

    assert await com(h, classify_financial_topic) is False


# --- resposta -------------------------------------------------------------

async def test_devolve_o_texto_da_resposta():
    h = resposta_do_modelo("O CDI e a taxa media dos emprestimos entre bancos.")

    texto = await com(h, answer_financial_question)

    assert texto.startswith("O CDI e a taxa")


async def test_falha_ao_responder_e_sinalizada():
    def h(request):
        return httpx.Response(500, json={"error": "indisponivel"})

    with pytest.raises(AIError):
        await com(h, answer_financial_question)


# --- contrato com o OpenRouter -------------------------------------------

async def test_usa_o_endpoint_e_os_modelos_configurados(monkeypatch):
    from app import ai

    monkeypatch.setattr(ai, "MODELO_GUARD", "provedor/modelo-barato")
    monkeypatch.setattr(ai, "MODELO_RESPOSTA", "provedor/modelo-forte")
    visto = {}

    def h(request):
        visto["path"] = request.url.path
        visto["body"] = json.loads(request.read())
        visto["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    await com(h, answer_financial_question)

    assert visto["path"].endswith("/chat/completions")
    assert visto["body"]["model"] == "provedor/modelo-forte"
    assert visto["auth"].startswith("Bearer ")


async def test_o_guard_usa_o_modelo_barato(monkeypatch):
    from app import ai

    monkeypatch.setattr(ai, "MODELO_GUARD", "provedor/modelo-barato")
    visto = {}

    def h(request):
        visto["body"] = json.loads(request.read())
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"is_financial": true}'}}]}
        )

    await com(h, classify_financial_topic)

    assert visto["body"]["model"] == "provedor/modelo-barato"
