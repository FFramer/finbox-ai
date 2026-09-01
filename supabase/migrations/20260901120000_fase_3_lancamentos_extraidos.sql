-- Finbox AI - fase 3: lancamentos extraidos dos documentos financeiros.
-- Tudo aqui e idempotente: rodar de novo num projeto ja aplicado nao muda nada.
--
-- Ate aqui a extracao vivia so dentro da background task que processava o
-- PDF: o resumo era enviado e as 25 linhas eram descartadas. Guardar em
-- coluna estruturada, e nao num blob de texto no contexto, e o que permite
-- somar e comparar em SQL -- o modelo nunca faz a conta.

create table if not exists public.transactions (
  id bigint generated always as identity primary key,
  conversation_id bigint not null
    references public.conversations(id) on delete cascade,
  message_id bigint not null
    references public.messages(id) on delete cascade,
  -- Ordem em que a linha aparece no documento. Junto com message_id da a
  -- idempotencia: reprocessar o mesmo evento substitui, nao duplica.
  position integer not null,
  -- Anulavel de proposito: o modelo as vezes devolve "02 AGO" em vez de
  -- data ISO, e perder a data de uma linha custa menos que perder a fatura.
  occurred_on date,
  description text not null,
  -- numeric, nunca float: centavo errado aqui contamina todo agregado.
  amount numeric(14,2) not null,
  category text not null default 'Outros',
  created_at timestamptz not null default now(),
  unique (message_id, position)
);

-- O unique acima ja indexa message_id. Falta a FK de conversation_id, que
-- o Postgres nao indexa sozinho: sem isto o cascade varre a tabela toda.
-- A data entra junto porque toda consulta por periodo vai filtrar por ela.
create index if not exists transactions_conversation_id_occurred_on_idx
  on public.transactions (conversation_id, occurred_on);

alter table public.transactions enable row level security;

revoke all on public.transactions from anon, authenticated;

-- delete faz parte do contrato: a RPC substitui os lancamentos do
-- documento em vez de acrescentar.
grant select, insert, delete on public.transactions to service_role;
grant usage, select on sequence public.transactions_id_seq to service_role;


create or replace function public.record_document_transactions(
  p_message_id bigint,
  p_transactions jsonb
)
returns integer
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_conversation_id bigint;
  v_total integer;
begin
  select message.conversation_id into v_conversation_id
  from public.messages as message
  where message.id = p_message_id;

  if v_conversation_id is null then
    raise exception 'mensagem % nao encontrada', p_message_id;
  end if;

  -- Substituir, nao acrescentar: o mesmo documento reprocessado tem que
  -- deixar a fatura igual.
  delete from public.transactions where message_id = p_message_id;

  -- Uma unica instrucao para as 25 linhas, em vez de 25 idas ao banco.
  insert into public.transactions (
    conversation_id,
    message_id,
    position,
    occurred_on,
    description,
    amount,
    category
  )
  select
    v_conversation_id,
    p_message_id,
    (item.linha ->> 'position')::integer,
    (item.linha ->> 'occurred_on')::date,
    item.linha ->> 'description',
    (item.linha ->> 'amount')::numeric,
    coalesce(nullif(item.linha ->> 'category', ''), 'Outros')
  from jsonb_array_elements(
    coalesce(p_transactions, '[]'::jsonb)
  ) as item(linha);

  get diagnostics v_total = row_count;

  return v_total;
end;
$$;

revoke all on function public.record_document_transactions(bigint, jsonb)
  from public, anon, authenticated;

grant execute on function public.record_document_transactions(bigint, jsonb)
  to service_role;
