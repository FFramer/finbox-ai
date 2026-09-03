"""Whitelist do Finbox: decide quem pode falar com o bot.

Regra determinística, sem IA. Nega por padrão: se nada estiver configurado,
ninguém passa. Um guard de segurança que falha aberto não e um guard.

A Evolution identifica o autor de duas formas, dependendo do contato e da
versão do WhatsApp:

    5511999999999@s.whatsapp.net   telefone
    222222222222222@lid            LID, não contem o telefone

O LID e estável por contato, mas só e descoberto capturando uma mensagem
daquela pessoa. Por isso os dois formatos são suportados.
"""

from app.config import (
    ADMIN_LID,
    ADMIN_PHONE,
    ALLOWED_GROUP_ID,
    ALLOWED_LID,
    ALLOWED_PHONE,
)

PHONE_SUFFIX = "@s.whatsapp.net"
LID_SUFFIX = "@lid"


def _digits(valor):
    return "".join(c for c in (valor or "") if c.isdigit())


def _author_is_allowed(author):
    if author.endswith(LID_SUFFIX):
        permitido = _digits(ALLOWED_LID)
        return bool(permitido) and _digits(author) == permitido

    permitido = _digits(ALLOWED_PHONE)
    return bool(permitido) and _digits(author) == permitido


def is_authorized(event):
    author = event.author_id

    if not author:
        return False

    # Em grupo, as duas condicoes valem: precisa ser o grupo permitido E
    # um autor autorizado. Estar no grupo não autoriza por si só.
    if event.is_group:
        if not ALLOWED_GROUP_ID:
            return False
        if _digits(event.chat_id) != _digits(ALLOWED_GROUP_ID):
            return False

    return _author_is_allowed(author)


def is_admin(event):
    """Diz se o autor e o dono da instancia.

    Portao de /reset, que apaga historico. Separado de is_authorized: estar
    autorizado a conversar nao autoriza a destruir a conversa.

    Nega quando nada esta configurado, pelo mesmo motivo que a allowlist
    nega: um guard que falha aberto nao e um guard.
    """
    author = event.author_id

    if not author:
        return False

    # Em grupo o autor chega como LID; so o telefone nunca casaria.
    if author.endswith(LID_SUFFIX):
        permitido = _digits(ADMIN_LID)
    else:
        permitido = _digits(ADMIN_PHONE)

    return bool(permitido) and _digits(author) == permitido
