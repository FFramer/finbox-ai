"""Analise de fatura: a IA extrai, o Python calcula.

Confiar no modelo para somar e a forma mais facil de entregar numero errado
com cara de certo.
"""

import httpx
import pytest

from app.analise import Transacao, analisar, resumir, somar


TRANSACOES = [
    Transacao("2026-08-12", "Uber *trip", 34.90, "Transporte"),
    Transacao("2026-08-12", "Uber *trip", 27.10, "Transporte"),
    Transacao("2026-08-03", "Spotify", 21.90, "Assinaturas"),
    Transacao("2026-08-15", "Restaurante Sao Jorge", 189.00, "Alimentacao"),
    Transacao("2026-08-20", "Netflix", 55.90, "Assinaturas"),
]


# --- calculo em Python ----------------------------------------------------

def test_soma_o_total_com_precisao_decimal():
    """float acumula erro em dinheiro; o total precisa fechar exato."""
    resultado = somar([Transacao("2026-08-01", "x", 0.10, "c")] * 3)

    assert str(resultado.total) == "0.30"


def test_conta_as_transacoes():
    assert somar(TRANSACOES).quantidade == 5


def test_agrupa_por_categoria_em_ordem_decrescente():
    categorias = somar(TRANSACOES).por_categoria

    assert [c for c, _ in categorias] == ["Alimentacao", "Assinaturas", "Transporte"]
    assert str(dict(categorias)["Transporte"]) == "62.00"


def test_identifica_a_maior_compra():
    maior = somar(TRANSACOES).maior

    assert maior.descricao == "Restaurante Sao Jorge"
    assert str(maior.valor) == "189.00"


def test_lista_vazia_nao_quebra():
    resultado = somar([])

    assert str(resultado.total) == "0.00"
    assert resultado.quantidade == 0
    assert resultado.maior is None


# --- extracao pela IA -----------------------------------------------------

async def test_extrai_transacoes_do_texto_do_pdf():
    import json

    conteudo = json.dumps({
        "transacoes": [
            {"data": "2026-08-12", "descricao": "Uber", "valor": 34.90,
             "categoria": "Transporte"},
        ]
    })

    def h(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": conteudo}}]}
        )

    transport = httpx.MockTransport(h)
    async with httpx.AsyncClient(base_url="https://or.exemplo", transport=transport) as c:
        transacoes = await analisar(c, "texto da fatura")

    assert len(transacoes) == 1
    assert transacoes[0].descricao == "Uber"
    assert str(transacoes[0].valor) == "34.90"


async def test_transacao_malformada_e_descartada_sem_derrubar_o_resto():
    import json

    conteudo = json.dumps({
        "transacoes": [
            {"data": "2026-08-12", "descricao": "Uber", "valor": 34.90},
            {"descricao": "sem valor"},
            {"data": "x", "descricao": "Netflix", "valor": "nao e numero"},
        ]
    })

    def h(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": conteudo}}]}
        )

    transport = httpx.MockTransport(h)
    async with httpx.AsyncClient(base_url="https://or.exemplo", transport=transport) as c:
        transacoes = await analisar(c, "texto")

    assert len(transacoes) == 1


# --- resumo ---------------------------------------------------------------

def test_resumo_traz_total_quantidade_e_categorias():
    texto = resumir(somar(TRANSACOES))

    assert "328,80" in texto
    assert "5" in texto
    assert "Alimentacao" in texto
    assert "Restaurante Sao Jorge" in texto


def test_resumo_de_fatura_sem_transacoes_e_honesto():
    texto = resumir(somar([]))

    assert "nenhuma" in texto.lower()
