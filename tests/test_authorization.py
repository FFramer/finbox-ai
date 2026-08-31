"""Whitelist: quem pode falar com o Finbox.

Regra deterministica, sem IA. Prepara os dois formatos de identificacao
que a Evolution pode enviar: telefone (@s.whatsapp.net) e LID (@lid).
"""

import pytest

from app import authorization
from app.authorization import is_authorized
from app.parser import ParsedEvent


def evento(author_id, is_group=False):
    return ParsedEvent(
        chat_id="120363400000000000@g.us" if is_group else author_id,
        from_me=False,
        is_group=is_group,
        text="oi",
        message_type="conversation",
        author_id=author_id,
        push_name="Fulano",
    )


@pytest.fixture
def config(monkeypatch):
    def aplicar(phone="", lid=""):
        monkeypatch.setattr(authorization, "ALLOWED_PHONE", phone)
        monkeypatch.setattr(authorization, "ALLOWED_LID", lid)

    return aplicar


# --- por telefone ---------------------------------------------------------

def test_autoriza_telefone_da_whitelist(config):
    config(phone="5511999999999")

    assert is_authorized(evento("5511999999999@s.whatsapp.net")) is True


def test_nega_telefone_fora_da_whitelist(config):
    config(phone="5511999999999")

    assert is_authorized(evento("5511888888888@s.whatsapp.net")) is False


def test_normaliza_simbolos_na_configuracao(config):
    config(phone="+55 (11) 99999-9999")

    assert is_authorized(evento("5511999999999@s.whatsapp.net")) is True


# --- por LID --------------------------------------------------------------

def test_autoriza_lid_da_whitelist(config):
    config(lid="222222222222222")

    assert is_authorized(evento("222222222222222@lid")) is True


def test_nega_lid_fora_da_whitelist(config):
    config(lid="222222222222222")

    assert is_authorized(evento("999999999999999@lid")) is False


def test_telefone_configurado_nao_autoriza_um_lid(config):
    """Os digitos de um LID nao sao um telefone; nao podem casar por acidente."""
    config(phone="222222222222222")

    assert is_authorized(evento("222222222222222@lid")) is False


# --- comportamento seguro por padrao --------------------------------------

def test_nega_tudo_quando_nada_esta_configurado(config):
    config()

    assert is_authorized(evento("5511999999999@s.whatsapp.net")) is False


def test_nega_quando_o_autor_nao_pode_ser_identificado(config):
    config(phone="5511999999999")

    assert is_authorized(evento(None)) is False


# --- grupo ----------------------------------------------------------------

def test_em_grupo_avalia_quem_escreveu_e_nao_o_grupo(config_grupo):
    """O identificador conferido e o do autor, nao o JID do grupo."""
    config_grupo(lid="222222222222222", grupo="120363400000000000@g.us")

    assert is_authorized(evento("222222222222222@lid", is_group=True)) is True


# --- grupo permitido ------------------------------------------------------

GRUPO_OK = "120363000000000@g.us"
GRUPO_OUTRO = "120363499999999999@g.us"


def evento_em_grupo(author_id, grupo=GRUPO_OK):
    return ParsedEvent(
        chat_id=grupo,
        from_me=False,
        is_group=True,
        text="oi",
        message_type="conversation",
        author_id=author_id,
        push_name="Fulano",
    )


@pytest.fixture
def config_grupo(monkeypatch):
    def aplicar(phone="", lid="", grupo=""):
        monkeypatch.setattr(authorization, "ALLOWED_PHONE", phone)
        monkeypatch.setattr(authorization, "ALLOWED_LID", lid)
        monkeypatch.setattr(authorization, "ALLOWED_GROUP_ID", grupo)

    return aplicar


def test_autoriza_autor_permitido_no_grupo_permitido(config_grupo):
    config_grupo(lid="222222222222222", grupo=GRUPO_OK)

    assert is_authorized(evento_em_grupo("222222222222222@lid")) is True


def test_nega_o_mesmo_autor_em_outro_grupo(config_grupo):
    config_grupo(lid="222222222222222", grupo=GRUPO_OK)

    evento = evento_em_grupo("222222222222222@lid", grupo=GRUPO_OUTRO)

    assert is_authorized(evento) is False


def test_nega_autor_desconhecido_dentro_do_grupo_permitido(config_grupo):
    """Estar no grupo nao basta; o autor tambem precisa estar autorizado."""
    config_grupo(lid="222222222222222", grupo=GRUPO_OK)

    assert is_authorized(evento_em_grupo("999999999999999@lid")) is False


def test_nega_qualquer_grupo_quando_nenhum_foi_configurado(config_grupo):
    config_grupo(lid="222222222222222", grupo="")

    assert is_authorized(evento_em_grupo("222222222222222@lid")) is False


def test_conversa_direta_nao_depende_do_grupo_configurado(config_grupo):
    config_grupo(phone="5511999999999", grupo="")

    assert is_authorized(evento("5511999999999@s.whatsapp.net")) is True
