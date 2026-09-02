"""Fases 2 e 3: lancamentos e comparacao entre faturas no contexto.

Os numeros deste bloco saem todos do Python. O modelo le uma tabela pronta
e verbaliza; comparar periodos nunca pode ser conta dele.
"""

from decimal import Decimal

import pytest

from app.lancamentos import agregar_por_mes, bloco_de_dados


def linha(message_id, position, data, descricao, valor, categoria):
    return {
        "message_id": message_id,
        "position": position,
        "occurred_on": data,
        "description": descricao,
        "amount": valor,
        "category": categoria,
    }


AGOSTO = [
    linha(67, 1, "2026-08-02", "SUPERMERCADO ZONA SUL", "186.42", "Mercado"),
    linha(67, 2, "2026-08-03", "UBER *TRIP", "27.90", "Transporte"),
    linha(67, 3, "2026-08-19", "IFOOD *RESTAURANTE", "63.40", "Alimentacao"),
]
JULHO = [
    linha(40, 1, "2026-07-05", "POSTO IPIRANGA", "200.00", "Transporte"),
    linha(40, 2, "2026-07-11", "NETFLIX.COM", "55.90", "Assinaturas"),
]


# --- fase 3: agregacao por mes -------------------------------------------

def test_agrega_totais_por_mes_em_ordem_cronologica():
    meses = agregar_por_mes(JULHO + AGOSTO)

    assert [m.mes for m in meses] == ["2026-07", "2026-08"]
    assert str(meses[0].total) == "255.90"
    assert str(meses[1].total) == "277.72"
    assert meses[1].quantidade == 3


def test_lancamento_sem_data_nao_entra_em_nenhum_mes():
    """Data que o modelo nao soube ler nao pode inventar um mes."""
    sem_data = [linha(67, 4, None, "PADARIA REAL", "28.50", "Alimentacao")]

    meses = agregar_por_mes(AGOSTO + sem_data)

    assert [m.mes for m in meses] == ["2026-08"]
    assert str(meses[0].total) == "277.72", "o valor sem data fica de fora"


def test_agregacao_vazia_nao_quebra():
    assert agregar_por_mes([]) == []


def test_soma_por_mes_usa_decimal_exato():
    """float acumularia erro de centavo entre faturas."""
    centavos = [
        linha(1, i, "2026-08-01", "x", "0.10", "c") for i in range(3)
    ]

    assert agregar_por_mes(centavos)[0].total == Decimal("0.30")


# --- fase 2: bloco de dados para o contexto ------------------------------

def test_bloco_traz_os_lancamentos_da_fatura_mais_recente():
    """Sem isto 'quanto gastei de Uber' nao tem como ser respondido."""
    bloco = bloco_de_dados(JULHO + AGOSTO)

    assert "UBER *TRIP" in bloco
    assert "R$ 27,90" in bloco
    assert "02/08" in bloco


def test_bloco_nao_lista_linha_a_linha_as_faturas_antigas():
    """So a mais recente vai detalhada; o resto entra como total do mes."""
    bloco = bloco_de_dados(JULHO + AGOSTO)

    assert "POSTO IPIRANGA" not in bloco
    assert "2026-07" in bloco
    assert "R$ 255,90" in bloco


def test_bloco_declara_que_os_totais_sao_do_finbox():
    """O modelo precisa saber que pode citar, e nao recalcular."""
    bloco = bloco_de_dados(AGOSTO)

    assert "Finbox" in bloco


def test_sem_lancamentos_nao_ha_bloco():
    assert bloco_de_dados([]) is None


def test_bloco_traz_totais_por_categoria_ja_somados():
    """Sem isto o modelo somaria linha a linha -- e a conta e dele, nao nossa."""
    bloco = bloco_de_dados(AGOSTO)

    assert "Alimentacao" in bloco
    # 186,42 de Mercado; 27,90 de Transporte; 63,40 de Alimentacao
    assert "R$ 186,42" in bloco
    linha_categoria = [l for l in bloco.splitlines() if l.startswith("Transporte")]
    assert linha_categoria == ["Transporte | R$ 27,90"]


def test_totais_por_categoria_somam_linhas_repetidas():
    repetidas = [
        linha(67, 1, "2026-08-03", "UBER *TRIP", "27.90", "Transporte"),
        linha(67, 2, "2026-08-10", "UBER *TRIP", "31.20", "Transporte"),
    ]

    bloco = bloco_de_dados(repetidas)

    assert "Transporte | R$ 59,10" in bloco
