-- Exercita reset_conversation contra o banco real e desfaz tudo no fim.
-- Cole no SQL Editor. Nada aqui persiste: o rollback fecha a transacao.
--
-- A suite Python simula a camada HTTP, entao nenhum teste de la executa o
-- plpgsql desta funcao. Falta de grant de delete, cascade que nao alcanca
-- uma tabela filha ou coluna ambigua so aparecem aqui.
--
-- Esperado:
--   a_antes           1 mensagem, 1 resumo, 2 lancamentos, 1 conversa
--   b_apagou          total = 1 (as mensagens que existiam)
--   c_depois          tudo zerado para aquela conversa
--   d_identidade      principal e identidade preservados

begin;

create temp table smoke_reset (conversation_id bigint, message_id bigint, created boolean);

insert into smoke_reset
select * from public.record_inbound_message(
  'primary', 'whatsapp', 'finbox-reset', '5598@s.whatsapp.net', '5598@s.whatsapp.net',
  'phone', 'SmokeReset', false, 'SMOKE-RESET-1', 'document', null, now(),
  '{"document": {"name": "fatura.pdf"}}'::jsonb);

select public.record_document_transactions(
  (select message_id from smoke_reset),
  '[{"position":1,"occurred_on":"2026-08-02","description":"MERCADO","amount":"10.00","category":"Mercado"},
    {"position":2,"occurred_on":"2026-08-03","description":"UBER","amount":"20.00","category":"Transporte"}]'::jsonb
);

insert into public.conversation_summaries (
  conversation_id, summary, covers_through_message_id,
  covered_message_count, model, prompt_version
)
values (
  (select conversation_id from smoke_reset), 'resumo contaminado',
  (select message_id from smoke_reset), 1, 'modelo', 'v1'
);

select 'a_antes' as etapa,
  (select count(*) from public.messages
     where conversation_id = (select conversation_id from smoke_reset)) as mensagens,
  (select count(*) from public.conversation_summaries
     where conversation_id = (select conversation_id from smoke_reset)) as resumos,
  (select count(*) from public.transactions
     where conversation_id = (select conversation_id from smoke_reset)) as lancamentos,
  (select count(*) from public.conversations
     where id = (select conversation_id from smoke_reset)) as conversas;

select 'b_apagou' as etapa,
  public.reset_conversation((select conversation_id from smoke_reset)) as total;

select 'c_depois' as etapa,
  (select count(*) from public.messages
     where conversation_id = (select conversation_id from smoke_reset)) as mensagens,
  (select count(*) from public.conversation_summaries
     where conversation_id = (select conversation_id from smoke_reset)) as resumos,
  (select count(*) from public.transactions
     where conversation_id = (select conversation_id from smoke_reset)) as lancamentos,
  (select count(*) from public.conversations
     where id = (select conversation_id from smoke_reset)) as conversas;

-- Quem o usuario e nao se apaga junto com o que ele conversou.
select 'd_identidade' as etapa, count(*) as identidades
from public.principal_identities
where external_id = '5598@s.whatsapp.net';

rollback;
