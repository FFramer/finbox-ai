"""Estado persistido no Supabase, via PostgREST."""

import httpx
import pytest

from app.state import SupabaseBotState

URL = "https://projeto.supabase.co"
KEY = "sb_secret_teste"


def store_com(handler):
    return SupabaseBotState(
        URL, KEY, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_le_o_estado_ativado_da_tabela():
    def h(request):
        return httpx.Response(200, json=[{"id": 1, "enabled": True}])

    assert store_com(h).is_enabled() is True


def test_le_o_estado_desativado_da_tabela():
    def h(request):
        return httpx.Response(200, json=[{"id": 1, "enabled": False}])

    assert store_com(h).is_enabled() is False


def test_envia_a_credencial_e_filtra_a_linha_unica():
    visto = {}

    def h(request):
        visto["apikey"] = request.headers.get("apikey")
        visto["auth"] = request.headers.get("authorization")
        visto["url"] = str(request.url)
        return httpx.Response(200, json=[{"id": 1, "enabled": True}])

    store_com(h).is_enabled()

    assert visto["apikey"] == KEY
    assert visto["auth"] == f"Bearer {KEY}"
    assert "id=eq.1" in visto["url"]


def test_grava_o_novo_estado():
    visto = {}

    def h(request):
        visto["method"] = request.method
        visto["body"] = request.read().decode()
        return httpx.Response(200, json=[{"id": 1, "enabled": False}])

    store_com(h).set_enabled(False)

    assert visto["method"] == "PATCH"
    assert '"enabled": false' in visto["body"] or '"enabled":false' in visto["body"]


def test_falha_de_leitura_nao_deixa_o_bot_respondendo():
    """Sem saber o estado, o seguro e assumir desativado."""
    def h(request):
        return httpx.Response(500, json={"erro": "indisponivel"})

    assert store_com(h).is_enabled() is False


def test_erro_de_rede_na_leitura_tambem_assume_desativado():
    def h(request):
        raise httpx.ConnectError("sem rede")

    assert store_com(h).is_enabled() is False


def test_falha_ao_gravar_e_sinalizada():
    def h(request):
        return httpx.Response(500, json={"erro": "indisponivel"})

    with pytest.raises(RuntimeError):
        store_com(h).set_enabled(True)
