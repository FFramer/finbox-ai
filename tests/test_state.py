"""Estado ativado/desativado do bot."""

from app.state import InMemoryBotState


def test_comeca_ativado():
    assert InMemoryBotState().is_enabled() is True


def test_desativa():
    estado = InMemoryBotState()

    estado.set_enabled(False)

    assert estado.is_enabled() is False


def test_reativa_depois_de_desativado():
    estado = InMemoryBotState()
    estado.set_enabled(False)

    estado.set_enabled(True)

    assert estado.is_enabled() is True


def test_estado_inicial_pode_ser_definido():
    assert InMemoryBotState(enabled=False).is_enabled() is False
