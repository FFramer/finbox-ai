"""Envio de mensagem de volta pelo WhatsApp."""

import httpx
import pytest

from app.adapters.evolution_adapter import EvolutionError, send_text


async def chamar(handler, destino="5511999999999@s.whatsapp.net", texto="ola"):
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://evo.exemplo", transport=transport
    ) as client:
        return await send_text(client, destino, texto)


@pytest.mark.asyncio
async def test_envia_no_formato_plano_da_v2():
    """A v2 recusa o formato aninhado da v1: instance requires property "text"."""
    visto = {}

    def h(request):
        import json
        visto.update(json.loads(request.read()))
        visto["path"] = request.url.path
        return httpx.Response(201, json={"key": {"id": "X"}})

    await chamar(h, destino="12345@g.us", texto="Finbox ativado.")

    assert visto["number"] == "12345@g.us"
    assert visto["text"] == "Finbox ativado."
    assert "textMessage" not in visto
    assert "/message/sendText/" in visto["path"]


@pytest.mark.asyncio
async def test_erro_http_vira_evolution_error():
    def h(request):
        return httpx.Response(400, json={"error": "Bad Request"})

    with pytest.raises(EvolutionError):
        await chamar(h)


@pytest.mark.asyncio
async def test_erro_de_rede_vira_evolution_error():
    def h(request):
        raise httpx.ConnectError("sem rede")

    with pytest.raises(EvolutionError):
        await chamar(h)
