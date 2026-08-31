"""Camada 4: reconhecer, baixar e validar documentos financeiros."""

import base64

import httpx
import pytest

from app.adapters.evolution_adapter import EvolutionError, baixar_midia
from app.documento import (
    LIMITE_BYTES,
    DocumentoInvalido,
    extrair_texto_pdf,
    validar_documento,
)
from app.parser import parse_event

PDF_MINIMO = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 99 9]>>endobj\n"
    b"trailer<</Root 1 0 R>>"
)


def evento_documento(nome="fatura.pdf", mime="application/pdf", tamanho=1024):
    return {
        "event": "messages.upsert",
        "instance": "finbox",
        "data": {
            "key": {"remoteJid": "551199@s.whatsapp.net", "fromMe": False, "id": "ABC"},
            "message": {
                "documentMessage": {
                    "fileName": nome,
                    "mimetype": mime,
                    "fileLength": str(tamanho),
                }
            },
            "messageType": "documentMessage",
        },
    }


# --- reconhecimento no payload -------------------------------------------

def test_reconhece_que_a_mensagem_traz_um_documento():
    evento = parse_event(evento_documento())

    assert evento.documento is not None
    assert evento.documento.nome == "fatura.pdf"
    assert evento.documento.mimetype == "application/pdf"
    assert evento.documento.tamanho == 1024
    assert evento.message_id == "ABC"


def test_mensagem_de_texto_nao_tem_documento():
    texto = {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": "x@s.whatsapp.net", "fromMe": False},
            "message": {"conversation": "oi"},
            "messageType": "conversation",
        },
    }

    assert parse_event(texto).documento is None


# --- validacao ------------------------------------------------------------

def test_aceita_pdf():
    validar_documento(parse_event(evento_documento()).documento)


@pytest.mark.parametrize(
    "mime,nome",
    [
        ("image/jpeg", "foto.jpg"),
        ("application/vnd.ms-excel", "planilha.xls"),
        ("text/plain", "notas.txt"),
    ],
)
def test_recusa_o_que_nao_e_pdf(mime, nome):
    doc = parse_event(evento_documento(nome=nome, mime=mime)).documento

    with pytest.raises(DocumentoInvalido) as erro:
        validar_documento(doc)

    assert "PDF" in str(erro.value)


def test_recusa_arquivo_grande_demais():
    doc = parse_event(evento_documento(tamanho=LIMITE_BYTES + 1)).documento

    with pytest.raises(DocumentoInvalido) as erro:
        validar_documento(doc)

    assert "grande" in str(erro.value).lower()


def test_aceita_arquivo_no_limite():
    validar_documento(parse_event(evento_documento(tamanho=LIMITE_BYTES)).documento)


# --- download -------------------------------------------------------------

async def test_baixa_a_midia_pela_chave_da_mensagem():
    """A v2 espera {"message": {"key": {...}}}."""
    import json
    visto = {}

    def h(request):
        visto["body"] = json.loads(request.read())
        visto["path"] = request.url.path
        return httpx.Response(
            200, json={"base64": base64.b64encode(PDF_MINIMO).decode()}
        )

    transport = httpx.MockTransport(h)
    async with httpx.AsyncClient(base_url="https://evo.exemplo", transport=transport) as c:
        dados = await baixar_midia(c, "ABC")

    assert visto["body"] == {"message": {"key": {"id": "ABC"}}}
    assert "/chat/getBase64FromMediaMessage/" in visto["path"]
    assert dados == PDF_MINIMO


async def test_falha_no_download_vira_evolution_error():
    def h(request):
        return httpx.Response(400, json={"message": ["Message not found"]})

    transport = httpx.MockTransport(h)
    async with httpx.AsyncClient(base_url="https://evo.exemplo", transport=transport) as c:
        with pytest.raises(EvolutionError):
            await baixar_midia(c, "ABC")


# --- extracao de texto ----------------------------------------------------

def test_recusa_conteudo_que_nao_e_pdf_de_verdade():
    """A extensao mente; os bytes nao."""
    with pytest.raises(DocumentoInvalido):
        extrair_texto_pdf(b"isto nao e um pdf")


def test_extrai_texto_de_um_pdf_valido():
    from pypdf import PdfWriter
    import io

    escritor = PdfWriter()
    escritor.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    escritor.write(buffer)

    # pagina em branco: extrai sem quebrar, mesmo sem texto
    assert extrair_texto_pdf(buffer.getvalue()) == ""
