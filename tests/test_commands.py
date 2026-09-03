"""Comandos de sistema: /ativar e /desativar."""

import pytest

from app.commands import Command, parse_command


@pytest.mark.parametrize("texto", ["/ativar", "  /ativar  ", "/ATIVAR", "/Ativar"])
def test_reconhece_ativar_em_variacoes(texto):
    assert parse_command(texto) is Command.ATIVAR


@pytest.mark.parametrize("texto", ["/desativar", "/DESATIVAR", " /Desativar "])
def test_reconhece_desativar_em_variacoes(texto):
    assert parse_command(texto) is Command.DESATIVAR


@pytest.mark.parametrize(
    "texto",
    ["quanto gastei?", "/ativar agora", "ativar", "", None, "/outro"],
)
def test_nao_reconhece_o_que_nao_e_comando(texto):
    assert parse_command(texto) is None


def test_reconhece_o_comando_de_reset():
    from app.commands import Command, parse_command

    assert parse_command("/reset") is Command.RESET
    assert parse_command("  /RESET  ") is Command.RESET


def test_reset_dentro_de_frase_nao_conta():
    """Mesma regra dos outros: apagar historico nao pode sair de conversa."""
    from app.commands import parse_command

    assert parse_command("pode dar /reset depois?") is None
