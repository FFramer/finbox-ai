# Finbox AI

> Assistente financeiro pessoal no WhatsApp, restrito a uma allowlist, que
> transforma mensagens e documentos financeiros em informação estruturada.

O Finbox recebe mensagens do WhatsApp através da [Evolution API](https://doc.evolution-api.com),
responde apenas assuntos financeiros e analisa faturas em PDF — somando os
valores em Python, nunca no modelo de linguagem.

---

## Como funciona

```
WhatsApp
   ↓
Evolution API  ──────────────  webhook autenticado por cabeçalho
   ↓
FastAPI
   ↓
guard: mensagem própria?  ──── ignora (evita loop de auto-resposta)
   ↓
guard: allowlist          ──── ignora quem não está autorizado
   ↓
comandos /ativar /desativar ── estado persistido no Supabase
   ↓
guard: é assunto financeiro? ─ recusa o que está fora do domínio
   ↓
┌─ texto ─────────────┐   ┌─ PDF ──────────────────┐
│ modelo responde     │   │ pypdf extrai o texto   │
│                     │   │ modelo extrai as linhas│
│                     │   │ Python soma e agrupa   │
└─────────────────────┘   └────────────────────────┘
   ↓
Evolution API → WhatsApp
```

### A decisão de arquitetura que mais importa

Na análise de documentos, **o modelo lê e o Python calcula**:

```
modelo  →  extrai transações estruturadas do texto do PDF
Python  →  soma, agrupa por categoria, ordena, formata
```

Modelos de linguagem erram aritmética de um jeito plausível: o número sai
errado com cara de certo. Nenhum valor do resumo passa pelo modelo — todos
saem de `Decimal` em Python, o que torna o total verificável e testável.

---

## Requisitos

- Python 3.11+
- Uma instância da [Evolution API](https://doc.evolution-api.com) v2 conectada ao WhatsApp
- Uma conta na [OpenRouter](https://openrouter.ai) (permite usar modelos de vários provedores)
- Um projeto no [Supabase](https://supabase.com) (opcional — sem ele o estado fica em memória)

## Instalação

```bash
git clone https://github.com/FFramer/finbox-ai.git
cd finbox-ai

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## Configuração

Copie `.env.example` para `.env` e preencha:

```bash
cp .env.example .env
```

| Variável | Obrigatória | Descrição |
|---|:---:|---|
| `EVOLUTION_API_URL` | sim | URL da sua Evolution API |
| `EVOLUTION_API_KEY` | sim | Chave global da Evolution |
| `EVOLUTION_INSTANCE` | sim | Nome da instância |
| `WEBHOOK_SECRET` | sim | Segredo que autentica o webhook |
| `ADMIN_TOKEN` | sim | Token das rotas administrativas |
| `ALLOWED_LID` | ao menos um | Identificador LID de quem pode usar |
| `ALLOWED_PHONE` | ao menos um | Telefone de quem pode usar (só dígitos) |
| `ALLOWED_GROUP_ID` | não | Grupo autorizado; vazio bloqueia todos |
| `OPENROUTER_API_KEY` | para IA | Chave da OpenRouter |
| `OPENROUTER_MODEL_GUARD` | para IA | Modelo do classificador |
| `OPENROUTER_MODEL_ANSWER` | para IA | Modelo das respostas e da extração |
| `SUPABASE_URL` | não | Sem ela o estado não sobrevive a restart |
| `SUPABASE_KEY` | não | Chave **secreta** (`service_role` / `sb_secret_`) |
| `EXPOSE_DOCS` | não | `1` publica `/docs`; padrão é desligado |

Gere os segredos com:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

A aplicação **recusa subir** se faltar variável obrigatória, listando todas de
uma vez — em vez de falhar mais tarde com um erro sem relação com a causa.

### Banco de dados

Se for usar o Supabase, crie a tabela de estado:

```sql
create table public.bot_state (
  id smallint primary key default 1,
  enabled boolean not null default true,
  updated_at timestamptz not null default now(),
  constraint bot_state_linha_unica check (id = 1)
);

alter table public.bot_state enable row level security;

-- Somente o backend acessa. Sem políticas, anon/authenticated não leem nada.
grant select, update on public.bot_state to service_role;
revoke all on public.bot_state from anon, authenticated;

insert into public.bot_state (id, enabled) values (1, true)
on conflict (id) do nothing;
```

RLS fica ligada **sem nenhuma política**: a tabela é inalcançável pela Data
API. O backend usa a chave secreta, que ignora RLS por design. Por isso a
chave publicável (`anon` / `sb_publishable_`) não funciona aqui.

## Execução

```bash
uvicorn app.main:app --reload
```

### Conectar à Evolution API

A Evolution precisa alcançar a aplicação por uma URL pública. Em
desenvolvimento, um túnel resolve:

```bash
cloudflared tunnel --url http://localhost:8000
```

Registre o webhook (a rota já configura o cabeçalho de autenticação):

```bash
curl -X POST "http://localhost:8000/setup-webhook?webhook_url=https://SEU-TUNEL/webhook" \
     -H "x-admin-token: $ADMIN_TOKEN"
```

Confirme o estado:

```bash
curl http://localhost:8000/evolution-check -H "x-admin-token: $ADMIN_TOKEN"
```

Em produção, rodar a aplicação **no mesmo host da Evolution API** dispensa o
túnel: o webhook aponta para o endereço interno e o evento nunca sai da máquina.

## Uso

| Mensagem | Resposta |
|---|---|
| `Como funciona o CDI?` | resposta financeira do modelo |
| `Quem ganhou o jogo ontem?` | recusa: fora do domínio |
| `/desativar` | `Finbox desativado.` |
| `/ativar` | `Finbox ativado.` |
| *fatura.pdf* | resumo com total, categorias e maior compra |

Exemplo de resumo:

```
Documento analisado

Total: R$ 978,85
Transações: 8

Principais categorias:
- Alimentação: R$ 501,45
- Transporte: R$ 312,00
- Saúde: R$ 87,60

Maior compra:
- R$ 312,45 - SUPERMERCADO
```

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

146 testes, sem chamadas de rede reais — a camada HTTP é simulada com
`httpx.MockTransport`, então a suíte roda offline e é determinística.

## Rotas

| Rota | Autenticação | Uso |
|---|---|---|
| `GET /health` | nenhuma | Health check |
| `POST /webhook` | `x-finbox-secret` | Recebe eventos da Evolution |
| `GET /evolution-check` | `x-admin-token` | Diagnóstico da instância |
| `POST /setup-webhook` | `x-admin-token` | Registra o webhook |

`/docs` e `/openapi.json` ficam desligados por padrão, porque a aplicação
costuma ficar exposta em uma URL pública para receber o webhook.

## Segurança

- **Allowlist determinística**, sem IA: quem não está autorizado é ignorado
  em silêncio. Nada configurado significa ninguém autorizado — o guard falha
  fechado.
- **Segredo do webhook em cabeçalho**, não na URL: a URL fica gravada no
  banco da Evolution e aparece em log.
- **Tokens separados** para o webhook e para as rotas administrativas. O
  primeiro trafega até a Evolution; se vazasse, não pode levar o segundo junto.
- **O guard de estado falha fechado**: se o Supabase estiver indisponível, o
  bot fica calado em vez de responder achando que está ligado.
- **`.env` fora do versionamento**, junto com QR codes e binários locais.

## Limitações conhecidas

- **O guard de domínio é uma barreira mole.** Um classificador por LLM pode
  ser contornado por injeção de prompt. O prompt instrui a tratar o conteúdo
  do usuário como texto, não como ordem, mas isso reduz o risco sem eliminá-lo.
  A allowlist é a barreira real de segurança.
- **PDF digitalizado não é lido.** Sem texto selecionável não há OCR; o
  Finbox avisa em vez de responder que a fatura está vazia.
- **Somente PDF**, até 10 MB.
- **Uma allowlist única**, sem múltiplos usuários.
- **Sem histórico de conversa**: cada mensagem é tratada isoladamente.
- **Categorização vem do modelo** e pode variar entre execuções; os valores,
  não — esses são calculados em Python.

## Licença

MIT
