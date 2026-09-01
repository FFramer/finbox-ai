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


async def test_resposta_invalida_do_modelo_nao_vira_lista_vazia():
    def h(request):
        return httpx.Response(
            200, json={'choices': [{'message': {'content': 'nao consegui'}}]},
        )

    transport = httpx.MockTransport(h)
    async with httpx.AsyncClient(base_url='https://or.exemplo', transport=transport) as c:
        with pytest.raises(RuntimeError, match='formato invalido'):
            await analisar(c, 'texto da fatura')


async def test_lista_vazia_explicita_continua_valida():
    import json

    def h(request):
        return httpx.Response(
            200,
            json={'choices': [{'message': {'content': json.dumps({'transacoes': []})}}]},
        )

    transport = httpx.MockTransport(h)
    async with httpx.AsyncClient(base_url='https://or.exemplo', transport=transport) as c:
        assert await analisar(c, 'texto da fatura') == []


# --- resumo ---------------------------------------------------------------

def test_resumo_traz_total_quantidade_e_categorias():
    texto = resumir(somar(TRANSACOES))

    assert "328,80" in texto
    assert "5" in texto
    assert "Alimentacao" in texto
    assert "Restaurante Sao Jorge" in texto


def test_resumo_de_fatura_sem_transacoes_e_honesto():
    texto = resumir(somar([]))

    assert 'não consegui identificar' in texto.lower()
    assert 'texto selecionável' not in texto.lower()


# --- pergunta do caption --------------------------------------------------

def _cliente_que_responde(conteudo, capturadas=None):
    """Cliente de IA falso que devolve `conteudo` e guarda o payload enviado."""
    def h(request):
        if capturadas is not None:
            import json as _json
            capturadas.append(_json.loads(request.content))
        return httpx.Response(
            200, json={'choices': [{'message': {'content': conteudo}}]}
        )

    return httpx.MockTransport(h)


async def test_responde_a_pergunta_que_veio_no_caption():
    from app.analise import responder_sobre

    transport = _cliente_que_responde(
        'Sim: encontrei lancamentos iguais no mesmo dia.'
    )
    async with httpx.AsyncClient(base_url='https://or.exemplo', transport=transport) as c:
        resposta = await responder_sobre(
            c, somar(TRANSACOES), TRANSACOES, 'tem cobranca duplicada?'
        )

    assert resposta == 'Sim: encontrei lancamentos iguais no mesmo dia.'


async def test_caption_generico_nao_gera_bloco_extra():
    """Sem pergunta de verdade, o resumo determinístico ja basta."""
    from app.analise import SEM_PERGUNTA, responder_sobre

    transport = _cliente_que_responde(SEM_PERGUNTA)
    async with httpx.AsyncClient(base_url='https://or.exemplo', transport=transport) as c:
        resposta = await responder_sobre(
            c, somar(TRANSACOES), TRANSACOES, 'analise minha fatura'
        )

    assert resposta is None


async def test_o_modelo_recebe_os_totais_calculados_pelo_python():
    """A garantia do projeto: o modelo cita numero do Python, nao inventa o seu."""
    from app.analise import responder_sobre

    capturadas = []
    transport = _cliente_que_responde('ok', capturadas)
    async with httpx.AsyncClient(base_url='https://or.exemplo', transport=transport) as c:
        await responder_sobre(c, somar(TRANSACOES), TRANSACOES, 'quanto gastei?')

    enviado = ' '.join(m['content'] for m in capturadas[0]['messages'])

    assert '328.80' in enviado
    assert 'Restaurante Sao Jorge' in enviado


async def test_resposta_com_valor_financeiro_inventado_e_rejeitada():
    from app.ai import AIError
    from app.analise import responder_sobre

    transport = _cliente_que_responde('O total foi R$ 999,00.')
    async with httpx.AsyncClient(
        base_url='https://or.exemplo', transport=transport
    ) as c:
        with pytest.raises(AIError, match='numero na resposta livre'):
            await responder_sobre(
                c, somar(TRANSACOES), TRANSACOES, 'quanto gastei?'
            )


async def test_valor_existente_nao_pode_ser_reclassificado_pelo_modelo():
    from app.ai import AIError
    from app.analise import responder_sobre

    # 189,00 existe, mas e a maior compra, nao o total.
    transport = _cliente_que_responde('O total foi R$ 189,00.')
    async with httpx.AsyncClient(
        base_url='https://or.exemplo', transport=transport
    ) as c:
        with pytest.raises(AIError, match='numero na resposta livre'):
            await responder_sobre(
                c, somar(TRANSACOES), TRANSACOES, 'quanto gastei?'
            )


async def test_valor_inventado_escrito_com_reais_tambem_e_rejeitado():
    from app.ai import AIError
    from app.analise import responder_sobre

    transport = _cliente_que_responde('O total foi 999 reais.')
    async with httpx.AsyncClient(
        base_url='https://or.exemplo', transport=transport
    ) as c:
        with pytest.raises(AIError, match='numero na resposta livre'):
            await responder_sobre(
                c, somar(TRANSACOES), TRANSACOES, 'quanto gastei?'
            )


async def test_percentual_calculado_pelo_modelo_e_rejeitado():
    from app.ai import AIError
    from app.analise import responder_sobre

    transport = _cliente_que_responde('Alimentacao representa 57,48% do total.')
    async with httpx.AsyncClient(
        base_url='https://or.exemplo', transport=transport
    ) as c:
        with pytest.raises(AIError, match='percentual nao fornecido'):
            await responder_sobre(
                c, somar(TRANSACOES), TRANSACOES, 'onde gastei mais?'
            )


def test_resumir_anexa_a_resposta_do_caption():
    texto = resumir(somar(TRANSACOES), extra='Sim, achei duas cobrancas iguais.')

    assert 'Total: R$ 328,80' in texto
    assert 'Sim, achei duas cobrancas iguais.' in texto


def test_resumir_sem_extra_continua_igual():
    assert resumir(somar(TRANSACOES)) == resumir(somar(TRANSACOES), extra=None)


async def test_marcador_parafraseado_pelo_modelo_nao_vaza_para_o_whatsapp():
    """Modelo pequeno devolve NO_PERGUNTA, SEM PERGUNTA., 'sem pergunta'..."""
    from app.analise import responder_sobre

    for variante in ('NO_PERGUNTA', 'SEM PERGUNTA.', 'sem_pergunta', 'Sem pergunta'):
        transport = _cliente_que_responde(variante)
        async with httpx.AsyncClient(
            base_url='https://or.exemplo', transport=transport
        ) as c:
            resposta = await responder_sobre(
                c, somar(TRANSACOES), TRANSACOES, 'analise minha fatura'
            )

        assert resposta is None, f'vazou: {variante!r}'


async def test_resposta_de_verdade_que_menciona_pergunta_nao_e_descartada():
    """O filtro do marcador nao pode comer uma resposta legitima."""
    from app.analise import responder_sobre

    legitima = (
        'Boa pergunta: o maior gasto foi Restaurante Sao Jorge, '
        'na categoria Alimentacao.'
    )
    transport = _cliente_que_responde(legitima)
    async with httpx.AsyncClient(base_url='https://or.exemplo', transport=transport) as c:
        resposta = await responder_sobre(
            c, somar(TRANSACOES), TRANSACOES, 'onde gastei mais?'
        )

    assert resposta == legitima
