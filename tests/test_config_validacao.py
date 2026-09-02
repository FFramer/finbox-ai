"""Item 7: falhar na subida com mensagem clara, nao com None no meio do caminho."""

import pytest

from app.config import ConfigInvalida, validar


COMPLETA = {
    "EVOLUTION_API_URL": "https://evo.exemplo",
    "EVOLUTION_API_KEY": "chave",
    "EVOLUTION_INSTANCE": "finbox",
    "OPENROUTER_API_KEY": "sk-or-teste",
    "OPENROUTER_MODEL_ANSWER": "modelo/resposta",
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


def test_supabase_exige_url_e_chave_em_conjunto():
    with pytest.raises(ConfigInvalida) as erro:
        validar(dict(COMPLETA, SUPABASE_URL="https://db.exemplo"))

    assert "SUPABASE_KEY" in str(erro.value)

    with pytest.raises(ConfigInvalida) as erro:
        validar(dict(COMPLETA, SUPABASE_KEY="sb_secret_teste"))

    assert "SUPABASE_URL" in str(erro.value)


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


@pytest.mark.parametrize(
    "faltando",
    ["OPENROUTER_API_KEY", "OPENROUTER_MODEL_ANSWER"],
)
def test_credenciais_da_ia_faltando_impedem_a_subida(faltando):
    """Sem elas a aplicacao sobe e falha uma mensagem por vez, como
    INDISPONIVEL, sem nada no log que aponte a configuracao."""
    valores = dict(COMPLETA, **{faltando: ""})

    with pytest.raises(ConfigInvalida) as erro:
        validar(valores)

    assert faltando in str(erro.value)


def test_valores_atuais_expoe_tudo_que_a_validacao_exige():
    """Uma obrigatoria fora de valores_atuais seria sempre reportada como
    ausente, mesmo preenchida no .env."""
    from app.config import OBRIGATORIAS, valores_atuais

    assert set(OBRIGATORIAS) <= set(valores_atuais())


def test_configuracao_do_guard_foi_removida():
    """Sem classificador nao ha consumidor legitimo dessas variaveis."""
    from app import config

    assert not hasattr(config, "OPENROUTER_MODEL_GUARD")
    assert not hasattr(config, "GUARD_WINDOW")
    assert "OPENROUTER_MODEL_GUARD" not in config.OBRIGATORIAS
    assert "OPENROUTER_MODEL_GUARD" not in config.valores_atuais()


def test_env_example_nao_oferece_mais_as_variaveis_do_guard():
    import pathlib as _p

    texto = _p.Path(".env.example").read_text(encoding="utf-8")

    assert "OPENROUTER_MODEL_GUARD" not in texto
    assert "GUARD_WINDOW" not in texto
