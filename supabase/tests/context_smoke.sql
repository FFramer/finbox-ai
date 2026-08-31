-- Fase 2: elegibilidade da janela e concorrencia do resumo.
-- Cole no SQL Editor. Nada persiste: o rollback fecha a transacao.

begin;

create temp table base as
select * from public.record_inbound_message(
  'primary','whatsapp','finbox','5599@s.whatsapp.net','5599@s.whatsapp.net',
  'phone','Smoke',false,'E-1','text','pergunta valida',now(),'{}'::jsonb);

-- Uma linha para cada motivo de descarte, mais as duas que devem passar.
insert into public.messages
  (conversation_id, direction, role, kind, content, processing_status, delivery_status)
select (select conversation_id from base), d, r, k, c, ps, ds
from (values
  ('inbound','user','command','/ativar','completed',null),
  ('inbound','user','text','ignorada','ignored',null),
  ('inbound','user','text','ainda nao processada','received',null),
  ('inbound','user','text','em processamento','processing',null),
  ('inbound','user','text','','completed',null),
  ('outbound','assistant','text','resposta entregue','completed','sent'),
  ('outbound','assistant','text','resposta que falhou','completed','failed'),
  ('internal','system','system','nota interna','completed',null)
) as v(d, r, k, c, ps, ds);

-- Mesmo predicado que o adapter envia ao PostgREST em FILTRO_ELEGIVEL.
-- Esperado: so 'em processamento' (a mensagem atual) e 'resposta entregue'.
select m.content, m.direction, m.processing_status, m.delivery_status,
  (m.role in ('user','assistant')
   and m.kind <> 'command'
   and m.content is not null and m.content <> ''
   and (
     (m.direction = 'inbound' and m.processing_status in ('completed','processing'))
     or (m.direction = 'outbound' and m.delivery_status = 'sent')
   )) as elegivel
from public.messages m
where m.conversation_id = (select conversation_id from base)
order by m.id;

-- Concorrencia do resumo. Esperado: true, false, true, false, final 'r3'.
create temp table r (etapa text, aplicado boolean);

insert into r select 'a_primeiro_resumo', public.save_conversation_summary(
  (select conversation_id from base), 'r1',
  (select min(id) from public.messages
    where conversation_id = (select conversation_id from base)),
  1, 'modelo', 'v1', null);

insert into r select 'b_repetido_sem_watermark', public.save_conversation_summary(
  (select conversation_id from base), 'r2',
  (select max(id) from public.messages
    where conversation_id = (select conversation_id from base)),
  2, 'modelo', 'v1', null);

insert into r select 'c_avanca_com_watermark_certo', public.save_conversation_summary(
  (select conversation_id from base), 'r3',
  (select max(id) from public.messages
    where conversation_id = (select conversation_id from base)),
  2, 'modelo', 'v1',
  (select min(id) from public.messages
    where conversation_id = (select conversation_id from base)));

insert into r select 'd_watermark_obsoleto', public.save_conversation_summary(
  (select conversation_id from base), 'r4',
  (select max(id) from public.messages
    where conversation_id = (select conversation_id from base)),
  2, 'modelo', 'v1',
  (select min(id) from public.messages
    where conversation_id = (select conversation_id from base)));

select r.etapa, r.aplicado,
       (select summary from public.conversation_summaries limit 1) as resumo_final
from r order by r.etapa;

rollback;
