import os

from dotenv import load_dotenv

load_dotenv()


class ConfigInvalida(RuntimeError):
    """Configuração incompleta detectada na subida da aplicação."""


def normalizar_url(valor):
    """Remove a barra final para não gerar '//caminho' ao montar URLs."""
    if valor is None:
        return None

    return valor.rstrip("/")


# OpenRouter fala o protocolo da OpenAI, mas serve modelos de vários
# provedores. Trocar de modelo e trocar estas strings.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = normalizar_url(
    os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
)
OPENROUTER_MODEL_GUARD = os.getenv("OPENROUTER_MODEL_GUARD")
OPENROUTER_MODEL_ANSWER = os.getenv("OPENROUTER_MODEL_ANSWER")
EVOLUTION_API_URL = normalizar_url(os.getenv("EVOLUTION_API_URL"))
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE")
ALLOWED_PHONE = os.getenv("ALLOWED_PHONE")
ALLOWED_LID = os.getenv("ALLOWED_LID")
ALLOWED_GROUP_ID = os.getenv("ALLOWED_GROUP_ID")
SUPABASE_URL = normalizar_url(os.getenv("SUPABASE_URL"))
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
EXPOSE_DOCS = os.getenv("EXPOSE_DOCS", "").lower() in ("1", "true", "sim")

OBRIGATORIAS = (
    "EVOLUTION_API_URL",
    "EVOLUTION_API_KEY",
    "EVOLUTION_INSTANCE",
    "WEBHOOK_SECRET",
    "ADMIN_TOKEN",
)


def validar(valores):
    """Verifica a configuração e lista tudo o que falta de uma vez.

    Falhar na subida evita o modo de erro pior: a aplicação sobe, monta
    'None/webhook/find/None' e falha longe da causa.
    """
    faltando = [nome for nome in OBRIGATORIAS if not (valores.get(nome) or "").strip()]

    if not (valores.get("ALLOWED_PHONE") or valores.get("ALLOWED_LID") or "").strip():
        faltando.append("ALLOWED_PHONE ou ALLOWED_LID (ao menos um)")

    if faltando:
        raise ConfigInvalida(
            "Configuração incompleta no .env: " + ", ".join(faltando)
        )


def valores_atuais():
    return {
        "EVOLUTION_API_URL": EVOLUTION_API_URL,
        "EVOLUTION_API_KEY": EVOLUTION_API_KEY,
        "EVOLUTION_INSTANCE": EVOLUTION_INSTANCE,
        "WEBHOOK_SECRET": WEBHOOK_SECRET,
        "ADMIN_TOKEN": ADMIN_TOKEN,
        "ALLOWED_PHONE": ALLOWED_PHONE,
        "ALLOWED_LID": ALLOWED_LID,
    }
