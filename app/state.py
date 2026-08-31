"""Estado ativado/desativado do Finbox.

A interface e mínima de propósito: trocar a implementação em memória pela
do Supabase não exige mudança em quem consome o estado.
"""

import httpx

from app.config import SUPABASE_KEY, SUPABASE_URL

TABELA = "bot_state"
LINHA = "eq.1"
TIMEOUT = 15.0


class InMemoryBotState:
    """Guarda o estado no processo. Não sobrevive a restart."""

    def __init__(self, enabled=True):
        self._enabled = enabled

    def is_enabled(self):
        return self._enabled

    def set_enabled(self, enabled):
        self._enabled = enabled


class SupabaseBotState:
    """Estado na tabela public.bot_state, via PostgREST.

    Exige a chave secreta (service_role / sb_secret_): a tabela tem RLS
    ligada e nenhuma política, então a chave pública não le nem escreve.
    """

    def __init__(self, url, key, client=None):
        self._url = f"{url.rstrip('/')}/rest/v1/{TABELA}"
        self._headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        self._client = client or httpx.Client(timeout=TIMEOUT)

    def is_enabled(self):
        # Se o estado não pode ser lido, o seguro e ficar calado: um bot
        # que responde por engano e pior que um bot mudo.
        try:
            response = self._client.get(
                self._url,
                params={"id": LINHA, "select": "enabled"},
                headers=self._headers,
            )
        except httpx.HTTPError:
            return False

        if response.is_error:
            return False

        linhas = response.json()

        return bool(linhas) and bool(linhas[0].get("enabled"))

    def set_enabled(self, enabled):
        try:
            response = self._client.patch(
                self._url,
                params={"id": LINHA},
                headers=self._headers,
                json={"enabled": enabled},
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Supabase inacessivel: {exc}") from exc

        if response.is_error:
            raise RuntimeError(
                f"Supabase recusou a gravacao ({response.status_code}): "
                f"{response.text}"
            )


def _criar_store():
    if SUPABASE_URL and SUPABASE_KEY:
        return SupabaseBotState(SUPABASE_URL, SUPABASE_KEY)

    return InMemoryBotState()


_store = _criar_store()


def get_state_store():
    return _store
