"""Análise de documento financeiro.

Divisão deliberada de responsabilidade:

    modelo  -> le o texto e extrai transações estruturadas
    Python  -> soma, agrupa, ordena e formata

Modelo de linguagem erra aritmética com naturalidade, e erra de um jeito
plausível: o número sai errado com cara de certo. Somar em Python torna o
total verificável.
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.ai import AIError, MODELO_RESPOSTA, _completar

CENTAVOS = Decimal("0.01")
MAX_CATEGORIAS = 5

PROMPT_EXTRACAO = (
    "Você extrai transações de faturas de cartão, extratos e relatórios "
    "financeiros.\n"
    "Responda APENAS com JSON no formato:\n"
    '{"transacoes": [{"data": "AAAA-MM-DD", "descricao": "texto", '
    '"valor": 0.00, "categoria": "texto"}]}\n'
    "Regras: use ponto como separador decimal; não calcule totais; não "
    "invente transações que não estejam no documento; se não houver "
    "transações, devolva a lista vazia.\n"
    "Categorias sugeridas: Alimentação, Transporte, Assinaturas, Saúde, "
    "Compras, Serviços, Lazer, Educação, Outros."
)


@dataclass(frozen=True)
class Transacao:
    data: str
    descricao: str
    valor: Decimal
    categoria: str

    def __post_init__(self):
        if not isinstance(self.valor, Decimal):
            object.__setattr__(self, "valor", _para_decimal(self.valor))


@dataclass(frozen=True)
class Resultado:
    total: Decimal
    quantidade: int
    por_categoria: list
    maior: Transacao | None


def _para_decimal(valor):
    """Passa por str: Decimal(0.1) carrega o erro binário do float."""
    return Decimal(str(valor)).quantize(CENTAVOS)


def somar(transacoes):
    """Todos os números do resumo saem daqui, nunca do modelo."""
    total = sum((t.valor for t in transacoes), Decimal("0")).quantize(CENTAVOS)

    acumulado = defaultdict(lambda: Decimal("0"))
    for t in transacoes:
        acumulado[t.categoria] += t.valor

    por_categoria = sorted(
        ((c, v.quantize(CENTAVOS)) for c, v in acumulado.items()),
        key=lambda par: par[1],
        reverse=True,
    )

    return Resultado(
        total=total,
        quantidade=len(transacoes),
        por_categoria=por_categoria,
        maior=max(transacoes, key=lambda t: t.valor, default=None),
    )


def _ler_transacoes(conteudo):
    """Le o JSON tolerando cerca de texto, como no classificador."""
    try:
        dados = json.loads(conteudo)
    except (ValueError, TypeError):
        achado = re.search(r"\{.*\}", conteudo or "", re.DOTALL)
        if not achado:
            return []
        try:
            dados = json.loads(achado.group(0))
        except ValueError:
            return []

    if not isinstance(dados, dict):
        return []

    transacoes = []
    for bruta in dados.get("transacoes") or []:
        # Uma linha malformada não pode invalidar a fatura inteira.
        try:
            transacoes.append(
                Transacao(
                    data=str(bruta["data"]) if bruta.get("data") else "",
                    descricao=str(bruta["descricao"]),
                    valor=_para_decimal(bruta["valor"]),
                    categoria=str(bruta.get("categoria") or "Outros"),
                )
            )
        except (KeyError, TypeError, InvalidOperation, ArithmeticError):
            continue

    return transacoes


async def analisar(client, texto_do_documento):
    """Extrai as transações do texto. Erros do provedor sobem como AIError."""
    conteudo = await _completar(
        client,
        MODELO_RESPOSTA,
        PROMPT_EXTRACAO,
        texto_do_documento,
        temperature=0.0,
    )

    return _ler_transacoes(conteudo)


def _moeda(valor):
    inteiro, _, centavos = f"{valor:.2f}".partition(".")
    milhar = f"{int(inteiro):,}".replace(",", ".")

    return f"R$ {milhar},{centavos}"


def resumir(resultado):
    """Monta o texto para o WhatsApp a partir dos números já calculados."""
    if resultado.quantidade == 0:
        return (
            "Li o documento, mas não encontrei nenhuma transação. "
            "Se for um extrato digitalizado, pode ser que o PDF não "
            "tenha texto selecionável."
        )

    linhas = [
        "Documento analisado",
        "",
        f"Total: {_moeda(resultado.total)}",
        f"Transações: {resultado.quantidade}",
    ]

    if resultado.por_categoria:
        linhas += ["", "Principais categorias:"]
        for categoria, valor in resultado.por_categoria[:MAX_CATEGORIAS]:
            linhas.append(f"- {categoria}: {_moeda(valor)}")

    if resultado.maior:
        linhas += [
            "",
            "Maior compra:",
            f"- {_moeda(resultado.maior.valor)} - {resultado.maior.descricao}",
        ]

    return "\n".join(linhas)


__all__ = ["Transacao", "Resultado", "analisar", "somar", "resumir", "AIError"]
