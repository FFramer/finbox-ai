"""Validação e leitura de documentos financeiros.

O MVP suporta apenas PDF: faturas de cartão, extratos e relatórios. Suportar
quinze formatos custa muito e entrega pouco no começo.
"""

import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MIMETYPES_ACEITOS = ("application/pdf",)
LIMITE_BYTES = 10 * 1024 * 1024
LIMITE_CARACTERES = 60_000


class DocumentoInvalido(ValueError):
    """Documento recusado antes de qualquer processamento."""


def validar_documento(documento):
    """Valida o que da para saber antes de baixar o arquivo."""
    if documento is None:
        raise DocumentoInvalido("Nenhum documento na mensagem")

    if documento.mimetype not in MIMETYPES_ACEITOS:
        raise DocumentoInvalido(
            "Por enquanto o Finbox lê apenas PDF "
            f"(recebido: {documento.mimetype})"
        )

    if documento.tamanho and documento.tamanho > LIMITE_BYTES:
        limite_mb = LIMITE_BYTES // (1024 * 1024)
        raise DocumentoInvalido(
            f"Arquivo grande demais (limite de {limite_mb} MB)"
        )


def extrair_texto_pdf(dados):
    """Extrai o texto do PDF.

    O mimetype vem do remetente e a extensão mente; os bytes não. Por isso a
    validação real acontece aqui, ao abrir o arquivo.
    """
    try:
        leitor = PdfReader(io.BytesIO(dados))
        paginas = [pagina.extract_text() or "" for pagina in leitor.pages]
    except (PdfReadError, OSError, ValueError) as exc:
        raise DocumentoInvalido(f"Não consegui ler o PDF: {exc}") from exc

    texto = "\n".join(paginas).strip()

    # Corta o que não cabe no contexto do modelo em vez de estourar a chamada.
    return texto[:LIMITE_CARACTERES]
