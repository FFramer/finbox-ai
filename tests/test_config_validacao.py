"""Item 7: falhar na subida com mensagem clara, nao com None no meio do caminho."""

import pytest

from app.config import ConfigInvalida, validar


COMPLETA = {
    "EVOLUTION_API_URL": "https://evo.exemplo",
    "EVOLUTION_API_KEY": "chave",
    "EVOLUTION_INSTANCE": "finbox",
    "WEBHOOK_SECRET": "segredo",
    "ADMIN_TOKEN": "token",
    "ALLOWED_LID": "123",
}


def test_configuracao_completa_passa():
    validar(COMPLETA)


@pytest.mark.parametrize(
    "faltando",
    ["EVOLUTION_API_URL", "EVOLUTION_API_KEY", "EVOLUTION_INSTANCE",
     "WEBHOOK_SECRET", "ADMIN_TOKEN"],
)
def test_variavel_obrigatoria_ausente_e_apontada_pelo_nome(faltando):
    valores = dict(COMPLETA, **{faltando: ""})

    with pytest.raises(ConfigInvalida) as erro:
        validar(valores)

    assert faltando in str(erro.value)


def test_exige_ao_menos_um_identificador_autorizado():
    valores = dict(COMPLETA, ALLOWED_LID="")

    with pytest.raises(ConfigInvalida) as erro:
        validar(valores)

    assert "ALLOWED_PHONE" in str(erro.value)
    assert "ALLOWED_LID" in str(erro.value)


def test_telefone_sozinho_ja_satisfaz_a_whitelist():
    validar(dict(COMPLETA, ALLOWED_LID="", ALLOWED_PHONE="5511999999999"))


def test_lista_todas_as_ausentes_de_uma_vez():
    with pytest.raises(ConfigInvalida) as erro:
        validar(dict(COMPLETA, EVOLUTION_API_KEY="", ADMIN_TOKEN=""))

    assert "EVOLUTION_API_KEY" in str(erro.value)
    assert "ADMIN_TOKEN" in str(erro.value)


def test_url_da_evolution_normaliza_barra_final():
    """Item 8: barra final geraria //webhook/find na URL montada."""
    from app.config import normalizar_url

    assert normalizar_url("https://evo.exemplo/") == "https://evo.exemplo"
    assert normalizar_url("https://evo.exemplo") == "https://evo.exemplo"
    assert normalizar_url(None) is None
