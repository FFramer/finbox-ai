# Histórico de conversas — Fase 1

Esta fase cria a fonte de verdade que o contexto e o RAG usarão depois. Ela
persiste mensagens, mas ainda não envia o histórico ao modelo nem cria
embeddings.

## Estrutura

- `principals`: pessoa canônica. Nesta versão, a allowlist representa o
  principal `primary`.
- `principal_identities`: aliases observados no WhatsApp, como telefone e LID.
- `conversations`: chat por canal, instância da Evolution e `remoteJid`.
- `messages`: entradas e saídas, estado de processamento, entrega, horário do
  provedor e vínculo entre pergunta e resposta.

A constraint `messages_provider_message_id_unico` sobre
`(conversation_id, provider_message_id)` torna o webhook idempotente. A função
`record_inbound_message` resolve principal, identidade e conversa e grava a
mensagem na mesma transação do PostgreSQL.

As RPCs apontam essa constraint **pelo nome**, e não pelas colunas. Como
`returns table (conversation_id ...)` declara uma variável plpgsql homônima da
coluna, `on conflict (conversation_id, ...)` é ambíguo e o Postgres recusa com
`42702`. A inferência por colunas não aceita qualificação por tabela, então
referenciar a constraint é a única saída — daí ela ter nome explícito em vez do
autogerado.

## Estado no Supabase

Aplicado em 31/08/2026 no projeto de produção, como migração registrada:

| Versão | Migração |
|---|---|
| `20260831182802` | `fase_1_historico_de_conversas` |
| `20260831182941` | `corrige_ambiguidade_conversation_id_nas_rpcs` |

Os dois arquivos ficam em [`supabase/migrations/`](../supabase/migrations/) e
são o histórico do que já rodou — não devem ser editados. O
[`supabase/schema.sql`](../supabase/schema.sql) é o **estado desejado**:
idempotente e completo, serve para levantar um projeto novo de uma vez e para
ler o esquema inteiro sem reconstruí-lo mentalmente a partir das migrações.
Mudança de esquema daqui em diante entra nos dois: uma migração nova e o
`schema.sql` atualizado.

Use em `SUPABASE_KEY` somente uma chave secreta de backend (`service_role` ou
`sb_secret_`). Nunca coloque essa chave no frontend.

## Verificar

[`supabase/tests/history_smoke.sql`](../supabase/tests/history_smoke.sql)
exercita as duas RPCs contra o banco real dentro de uma transação revertida:
grava uma entrada, reenvia o mesmo `provider_message_id` e grava a resposta.
Nada persiste. Foi ele que pegou o `42702` — as funções só falham quando o
plpgsql executa, e a suíte Python simula a camada HTTP, então nenhum teste de
lá alcança esse erro.

Última execução: a mensagem reenviada devolveu `created = false` com a mesma
`message_id`, e a resposta saiu ligada à entrada por `reply_to_message_id`.

Fim a fim, resta enviar uma mensagem real pelo WhatsApp e conferir as linhas em
`principal_identities`, `conversations` e `messages` no Table Editor.

## Segurança

As quatro tabelas têm RLS habilitada e nenhum acesso concedido a `anon` ou
`authenticated`. Somente `service_role` recebe os privilégios necessários.
As duas RPCs usam `security invoker`, e a permissão de execução também fica
restrita ao backend.

O linter do Supabase aponta `rls_enabled_no_policy` (nível INFO) nas quatro —
isso é o desenho, não um defeito: sem política nenhuma, a tabela é inalcançável
pela Data API, e o backend passa por cima disso com a chave secreta.

O webhook não imprime mais o payload bruto. O log contém apenas tipo do evento,
instância e ID da mensagem, evitando registrar conteúdo financeiro e IDs de
chat sem necessidade.

## Fase 2 — contexto no prompt

Aplicada em `20260831205703`. O webhook passou a montar contexto antes de
chamar o modelo: resumo da conversa, janela das últimas mensagens elegíveis e
a mensagem atual.

`app/memory.py` concentra tudo — filtros, janela, teto, fallback e
concorrência. O `main.py` apenas pede o contexto e, depois de responder, pede
uma eventual atualização do resumo. Quando o RAG entrar, será mais um bloco
montado ali dentro, sem tocar no webhook.

**Elegibilidade.** A janela sai filtrada do banco, nunca depois do `LIMIT` —
filtrar depois deixaria mensagens descartadas ocupando espaço e empurrando as
úteis para fora. Ficam de fora: comandos, mensagens ignoradas, respostas com
`delivery_status` diferente de `sent` (o usuário não as viu; incluí-las faria
o modelo acreditar que disse algo que não chegou), conteúdo vazio e papéis
fora de `user`/`assistant`. A ordenação é por `messages.id`, a sequência
canônica — `occurred_at` vem do provedor e pode empatar ou chegar fora de
ordem.

**Isolamento entre mensagens simultâneas.** A janela é fechada em
`up_to_id = inbound.message_id`. Duas mensagens da mesma conversa processadas
em paralelo não se enxergam fora de ordem.

**Injeção.** O `ai.py` nunca recebe um `messages[]` pronto: ele recebe a
conversa filtrada e monta o próprio prompt de sistema. Histórico, resumo e o
RAG futuro entram só como `user`/`assistant`, e o resumo vai delimitado como
dado. Sem isso, bastaria uma mensagem gravada com `role: system` para
reescrever as regras do assistente.

**Resumo rolling.** Roda depois da resposta entregue **e gravada**: se o
`record_outbound` falhar, o watermark não avança, senão a troca que o usuário
viu se perderia. A gravação usa concorrência otimista — só grava se
`covers_through_message_id` ainda for o esperado, e o watermark nunca
retrocede. O lote é limitado a `SUMMARY_EVERY + 1`, e o resto entra na
passada seguinte.

**Teto duplo.** `HISTORY_WINDOW` conta mensagens; `HISTORY_MAX_CHARS` corta
por tamanho. Contagem sozinha não protege contra uma única mensagem enorme,
como o resumo de uma fatura.

**Degradação.** Ao contrário da escrita, a leitura do contexto é best-effort:
se o banco não responder, o Finbox responde sem memória — mas nunca sem a
pergunta atual, que é remontada à mão nesse caminho.

**Mensagens órfãs.** Um restart mata as `BackgroundTasks` em voo, e a
mensagem fica em `processing` para sempre — que desde a Fase 2 continua
elegível, aparecendo em toda janela futura daquela conversa. A subida varre
essas linhas e as marca como `failed` / `orphaned`, usando o
`messages_pending_idx`. O corte por idade (`STUCK_AFTER_MINUTES`) existe para
que, com mais de uma réplica, uma subida não mate o trabalho em voo da outra.

Verificação: [`supabase/tests/context_smoke.sql`](../supabase/tests/context_smoke.sql)
e [`supabase/tests/history_smoke.sql`](../supabase/tests/history_smoke.sql).

## Próxima fase

`memory_chunks` e `pgvector`: embeddings serão um índice derivado, nunca a
fonte de verdade do histórico. Entram como mais um bloco em
`ConversationMemory.build_context`.
