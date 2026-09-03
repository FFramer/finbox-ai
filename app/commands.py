"""Comandos de sistema reconhecidos no corpo da mensagem."""

from enum import Enum


class Command(Enum):
    ATIVAR = "/ativar"
    DESATIVAR = "/desativar"
    RESET = "/reset"


_POR_TEXTO = {c.value: c for c in Command}


def parse_command(texto):
    """Devolve o Command, ou None se a mensagem não for um comando.

    Exige o comando sozinho na mensagem: '/ativar agora' não conta, para
    não disparar ação a partir de uma frase que só menciona o comando.
    """
    if not texto:
        return None

    return _POR_TEXTO.get(texto.strip().lower())
