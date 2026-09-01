"""Extracao de dados do evento MESSAGES_UPSERT da Evolution.

Construido sobre payload real capturado da instancia (ver tests/fixtures/).
"""

import json
import pathlib

import pytest

from app.parser import parse_event

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def carregar(nome):
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


@pytest.fixture
def evento_real():
    return carregar("messages_upsert_grupo_fromme.json")


# --- guard de fromMe ------------------------------------------------------

def test_marca_como_propria_a_mensagem_enviada_pela_instancia(evento_real):
    assert parse_event(evento_real).from_me is True


def test_marca_como_alheia_a_mensagem_recebida(evento_real):
    evento_real["data"]["key"]["fromMe"] = False

    assert parse_event(evento_real).from_me is False


# --- extracao de texto ----------------------------------------------------

def test_extrai_o_texto_de_uma_mensagem_simples(evento_real):
    assert parse_event(evento_real).text == "teste finbox"


def test_extrai_o_texto_de_mensagem_com_link_ou_resposta(evento_real):
    evento_real["data"]["message"] = {
        "extendedTextMessage": {"text": "olha isso https://exemplo.com"}
    }
    evento_real["data"]["messageType"] = "extendedTextMessage"

    assert parse_event(evento_real).text == "olha isso https://exemplo.com"


def test_texto_fica_none_quando_a_mensagem_nao_tem_texto(evento_real):
    evento_real["data"]["message"] = {"imageMessage": {"mimetype": "image/jpeg"}}
    evento_real["data"]["messageType"] = "imageMessage"

    assert parse_event(evento_real).text is None


# --- documentos ----------------------------------------------------------

def test_extrai_tamanho_de_documento_serializado_como_long(evento_real):
    evento_real['data']['message'] = {
        'documentMessage': {
            'fileName': 'fatura.pdf',
            'mimetype': 'application/pdf',
            'fileLength': {'low': 1024, 'high': 0, 'unsigned': True},
        }
    }

    assert parse_event(evento_real).documento.tamanho == 1024


def test_documento_com_tamanho_objeto_desconhecido_nao_quebra(evento_real):
    evento_real['data']['message'] = {
        'documentMessage': {
            'fileName': 'fatura.pdf',
            'mimetype': 'application/pdf',
            'fileLength': {'formato': 'inesperado'},
        }
    }

    assert parse_event(evento_real).documento.tamanho is None


# --- origem ---------------------------------------------------------------

def test_identifica_mensagem_vinda_de_grupo(evento_real):
    assert parse_event(evento_real).is_group is True
    assert parse_event(evento_real).chat_id.endswith("@g.us")


def test_identifica_mensagem_vinda_de_conversa_direta(evento_real):
    evento_real["data"]["key"]["remoteJid"] = "5511999999999@s.whatsapp.net"

    resultado = parse_event(evento_real)

    assert resultado.is_group is False
    assert resultado.chat_id == "5511999999999@s.whatsapp.net"


# --- roteamento -----------------------------------------------------------

def test_ignora_evento_que_nao_e_mensagem_nova(evento_real):
    evento_real["event"] = "connection.update"

    assert parse_event(evento_real) is None


def test_ignora_payload_sem_estrutura_de_evento():
    assert parse_event({"foo": "bar"}) is None


# --- identificacao do autor -----------------------------------------------

def test_em_grupo_o_autor_e_o_participant(evento_real):
    assert parse_event(evento_real).author_id == "100390000000000@lid"


def test_em_conversa_direta_o_autor_e_o_proprio_remetente(evento_real):
    """Conversa direta nao traz 'participant'; o autor e o remoteJid."""
    evento_real["data"]["key"] = {
        "remoteJid": "5511999999999@s.whatsapp.net",
        "fromMe": False,
    }

    assert parse_event(evento_real).author_id == "5511999999999@s.whatsapp.net"


def test_extrai_instancia_e_timestamp_do_provedor(evento_real):
    evento_real["instance"] = "finbox"
    evento_real["data"]["messageTimestamp"] = "1788134400"

    resultado = parse_event(evento_real)

    assert resultado.instance == "finbox"
    assert resultado.occurred_at.isoformat() == "2026-08-31T00:00:00+00:00"
