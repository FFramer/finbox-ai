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


def _inteiro(nome, padrao):
    try:
        valor = int(os.getenv(nome, "") or padrao)
    except ValueError:
        return padrao
    return valor if valor > 0 else padrao


# Contexto de conversa. Contagem de mensagens e barata de raciocinar, mas
# uma unica mensagem pode ser enorme (o resumo de uma fatura) -- por isso
# o teto de caracteres tambem existe. A janela vale para a unica chamada
# que responde: nao existe mais janela reduzida de classificador.
HISTORY_WINDOW = _inteiro("HISTORY_WINDOW", 20)
SUMMARY_EVERY = _inteiro("SUMMARY_EVERY", 20)
HISTORY_MAX_CHARS = _inteiro("HISTORY_MAX_CHARS", 12000)

# Mensagem em processing por mais que isso so pode ser orfa de um
# processo que morreu com a background task em voo.
STUCK_AFTER_MINUTES = _inteiro("STUCK_AFTER_MINUTES", 15)

# OPENROUTER_BASE_URL fica de fora: tem default. As outras três não têm
# substituto — sem elas cada mensagem falha isolada, longe da causa.
OBRIGATORIAS = (
    "EVOLUTION_API_URL",
    "EVOLUTION_API_KEY",
    "EVOLUTION_INSTANCE",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL_ANSWER",
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

    supabase_url = (valores.get("SUPABASE_URL") or "").strip()
    supabase_key = (valores.get("SUPABASE_KEY") or "").strip()
    if bool(supabase_url) != bool(supabase_key):
        faltando.append("SUPABASE_KEY" if supabase_url else "SUPABASE_URL")

    if faltando:
        raise ConfigInvalida(
            "Configuração incompleta no .env: " + ", ".join(faltando)
        )


def valores_atuais():
    return {
        "EVOLUTION_API_URL": EVOLUTION_API_URL,
        "EVOLUTION_API_KEY": EVOLUTION_API_KEY,
        "EVOLUTION_INSTANCE": EVOLUTION_INSTANCE,
        "OPENROUTER_API_KEY": OPENROUTER_API_KEY,
        "OPENROUTER_MODEL_ANSWER": OPENROUTER_MODEL_ANSWER,
        "WEBHOOK_SECRET": WEBHOOK_SECRET,
        "ADMIN_TOKEN": ADMIN_TOKEN,
        "ALLOWED_PHONE": ALLOWED_PHONE,
        "ALLOWED_LID": ALLOWED_LID,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_KEY": SUPABASE_KEY,
    }
