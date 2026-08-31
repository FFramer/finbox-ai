-- Exercita as duas RPCs contra o banco real e desfaz tudo no fim.
-- Cole no SQL Editor. Nada aqui persiste: o rollback fecha a transacao.
--
-- Foi este teste que pegou o 42702 ('conversation_id is ambiguous'): as duas
-- funcoes so falham quando o plpgsql executa, e a suite Python simula a
-- camada HTTP, entao nenhum teste de la alcanca este erro.
--
-- Esperado:
--   a_inbound            created = true,  status received
--   b_inbound_reenviada  created = false, mesma message_id da etapa anterior
--   c_outbound           created = true,  reply_to_message_id = a_inbound

begin;

create temp table smoke_resultado (
  etapa text,
  conversation_id bigint,
  message_id bigint,
  created boolean
);

insert into smoke_resultado
select 'a_inbound', * from public.record_inbound_message(
  'primary', 'whatsapp', 'finbox', '5599@s.whatsapp.net', '5599@s.whatsapp.net',
  'phone', 'Smoke', false, 'SMOKE-1', 'text', 'ola', now(), '{}'::jsonb);

-- Mesmo provider_message_id: e o reenvio da Evolution apos um timeout.
insert into smoke_resultado
select 'b_inbound_reenviada', * from public.record_inbound_message(
  'primary', 'whatsapp', 'finbox', '5599@s.whatsapp.net', '5599@s.whatsapp.net',
  'phone', 'Smoke', false, 'SMOKE-1', 'text', 'ola', now(), '{}'::jsonb);

insert into smoke_resultado
select 'c_outbound', * from public.record_outbound_message(
  (select conversation_id from smoke_resultado where etapa = 'a_inbound'),
  (select message_id from smoke_resultado where etapa = 'a_inbound'),
  'SMOKE-OUT-1', 'resposta', 'sent', '{}'::jsonb);

select r.etapa, r.message_id, r.created, m.direction, m.role, m.kind,
       m.processing_status, m.delivery_status, m.reply_to_message_id,
       m.author_identity_id is not null as tem_identidade
from smoke_resultado r
join public.messages m on m.id = r.message_id
order by r.etapa;

-- Varredura de orfas. Esperado: a presa vira failed/orphaned; a recente e a
-- concluida ficam intactas.
insert into public.messages
  (conversation_id, direction, role, kind, content, processing_status, created_at)
values
  ((select conversation_id from smoke_resultado limit 1),
   'inbound','user','text','presa ha 1 hora','processing', now() - interval '1 hour'),
  ((select conversation_id from smoke_resultado limit 1),
   'inbound','user','text','processing recente','processing', now());

update public.messages
   set processing_status = 'failed', ignored_reason = 'orphaned'
 where processing_status = 'processing'
   and created_at < now() - interval '15 minutes';

select content, processing_status, ignored_reason
from public.messages
where conversation_id = (select conversation_id from smoke_resultado limit 1)
order by id;

rollback;
