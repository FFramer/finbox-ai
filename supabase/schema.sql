-- Finbox AI - esquema completo: estado do bot e fase 1 do historico.
-- Estado desejado do banco. O historico do que foi aplicado esta em migrations/.
-- O backend deve usar service_role/sb_secret.
-- Tudo aqui e idempotente: rodar de novo num projeto ja aplicado nao muda nada.

begin;

-- Estado ativado/desativado, lido por app/state.py. Fica neste arquivo para
-- que aplicar o esquema num projeto novo baste para a aplicacao subir.
create table if not exists public.bot_state (
  id smallint primary key default 1,
  enabled boolean not null default true,
  updated_at timestamptz not null default now(),
  constraint bot_state_linha_unica check (id = 1)
);

alter table public.bot_state enable row level security;

revoke all on public.bot_state from anon, authenticated;
grant select, update on public.bot_state to service_role;

insert into public.bot_state (id, enabled) values (1, true)
on conflict (id) do nothing;

create table if not exists public.principals (
  id bigint generated always as identity primary key,
  external_key text not null unique,
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.principal_identities (
  id bigint generated always as identity primary key,
  principal_id bigint not null references public.principals(id) on delete cascade,
  channel text not null,
  external_id text not null,
  identity_type text not null default 'unknown'
    check (identity_type in ('phone', 'lid', 'unknown')),
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (channel, external_id)
);

create table if not exists public.conversations (
  id bigint generated always as identity primary key,
  principal_id bigint not null references public.principals(id) on delete restrict,
  channel text not null,
  provider_instance text not null,
  external_chat_id text not null,
  is_group boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (channel, provider_instance, external_chat_id)
);

create table if not exists public.messages (
  id bigint generated always as identity primary key,
  conversation_id bigint not null
    references public.conversations(id) on delete cascade,
  author_identity_id bigint
    references public.principal_identities(id) on delete set null,
  reply_to_message_id bigint references public.messages(id) on delete set null,
  provider_message_id text,
  direction text not null check (direction in ('inbound', 'outbound', 'internal')),
  role text not null check (role in ('user', 'assistant', 'system', 'tool')),
  kind text not null check (kind in ('text', 'document', 'command', 'system')),
  content text,
  processing_status text not null default 'received'
    check (processing_status in ('received', 'processing', 'completed', 'ignored', 'failed')),
  delivery_status text
    check (delivery_status is null or delivery_status in ('queued', 'sent', 'failed')),
  ignored_reason text,
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  constraint messages_provider_message_id_unico
    unique (conversation_id, provider_message_id)
);

create index if not exists principal_identities_principal_id_idx
  on public.principal_identities (principal_id);
create index if not exists conversations_principal_id_idx
  on public.conversations (principal_id);
create index if not exists messages_conversation_timeline_idx
  on public.messages (conversation_id, occurred_at desc, id desc);
create index if not exists messages_author_identity_id_idx
  on public.messages (author_identity_id);
create index if not exists messages_reply_to_message_id_idx
  on public.messages (reply_to_message_id);
create index if not exists messages_pending_idx
  on public.messages (processing_status, created_at)
  where processing_status in ('received', 'processing');

alter table public.principals enable row level security;
alter table public.principal_identities enable row level security;
alter table public.conversations enable row level security;
alter table public.messages enable row level security;

revoke all on public.principals from anon, authenticated;
revoke all on public.principal_identities from anon, authenticated;
revoke all on public.conversations from anon, authenticated;
revoke all on public.messages from anon, authenticated;

grant select, insert, update on public.principals to service_role;
grant select, insert, update on public.principal_identities to service_role;
grant select, insert, update on public.conversations to service_role;
grant select, insert, update on public.messages to service_role;
grant usage, select on sequence public.principals_id_seq to service_role;
grant usage, select on sequence public.principal_identities_id_seq to service_role;
grant usage, select on sequence public.conversations_id_seq to service_role;
grant usage, select on sequence public.messages_id_seq to service_role;

create or replace function public.record_inbound_message(
  p_principal_key text,
  p_channel text,
  p_instance text,
  p_chat_id text,
  p_author_id text,
  p_identity_type text,
  p_display_name text,
  p_is_group boolean,
  p_provider_message_id text,
  p_kind text,
  p_content text,
  p_occurred_at timestamptz,
  p_metadata jsonb
)
returns table (conversation_id bigint, message_id bigint, created boolean)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_principal_id bigint;
  v_identity_id bigint;
  v_conversation_id bigint;
  v_message_id bigint;
  v_created boolean;
begin
  insert into public.principals as principal (external_key, display_name)
  values (p_principal_key, p_display_name)
  on conflict (external_key) do update
    set display_name = coalesce(excluded.display_name, principal.display_name),
        updated_at = now()
  returning principal.id into v_principal_id;

  if p_author_id is not null then
    insert into public.principal_identities as identity (
      principal_id, channel, external_id, identity_type, display_name
    )
    values (
      v_principal_id, p_channel, p_author_id, p_identity_type, p_display_name
    )
    on conflict (channel, external_id) do update
      set display_name = coalesce(excluded.display_name, identity.display_name),
          updated_at = now()
    returning identity.id into v_identity_id;
  end if;

  insert into public.conversations as conversation (
    principal_id, channel, provider_instance, external_chat_id, is_group
  )
  values (
    v_principal_id, p_channel, p_instance, p_chat_id, p_is_group
  )
  on conflict (channel, provider_instance, external_chat_id) do update
    set is_group = excluded.is_group,
        updated_at = now()
  returning conversation.id into v_conversation_id;

  insert into public.messages as message (
    conversation_id,
    author_identity_id,
    provider_message_id,
    direction,
    role,
    kind,
    content,
    processing_status,
    occurred_at,
    metadata
  )
  values (
    v_conversation_id,
    v_identity_id,
    p_provider_message_id,
    'inbound',
    'user',
    p_kind,
    p_content,
    'received',
    coalesce(p_occurred_at, now()),
    coalesce(p_metadata, '{}'::jsonb)
  )
  on conflict on constraint messages_provider_message_id_unico do nothing
  returning message.id into v_message_id;

  v_created := found;
  if not v_created then
    select existing.id into v_message_id
    from public.messages as existing
    where existing.conversation_id = v_conversation_id
      and existing.provider_message_id = p_provider_message_id;
  end if;

  return query select v_conversation_id, v_message_id, v_created;
end;
$$;

create or replace function public.record_outbound_message(
  p_conversation_id bigint,
  p_reply_to_message_id bigint,
  p_provider_message_id text,
  p_content text,
  p_delivery_status text,
  p_metadata jsonb
)
returns table (conversation_id bigint, message_id bigint, created boolean)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_message_id bigint;
  v_created boolean;
begin
  insert into public.messages as message (
    conversation_id,
    reply_to_message_id,
    provider_message_id,
    direction,
    role,
    kind,
    content,
    processing_status,
    delivery_status,
    metadata
  )
  values (
    p_conversation_id,
    p_reply_to_message_id,
    p_provider_message_id,
    'outbound',
    'assistant',
    'text',
    p_content,
    'completed',
    p_delivery_status,
    coalesce(p_metadata, '{}'::jsonb)
  )
  on conflict on constraint messages_provider_message_id_unico do nothing
  returning message.id into v_message_id;

  v_created := found;
  if not v_created then
    select existing.id into v_message_id
    from public.messages as existing
    where existing.conversation_id = p_conversation_id
      and existing.provider_message_id = p_provider_message_id;
  end if;

  return query select p_conversation_id, v_message_id, v_created;
end;
$$;

revoke all on function public.record_inbound_message(
  text, text, text, text, text, text, text, boolean,
  text, text, text, timestamptz, jsonb
) from public, anon, authenticated;
revoke all on function public.record_outbound_message(
  bigint, bigint, text, text, text, jsonb
) from public, anon, authenticated;

grant execute on function public.record_inbound_message(
  text, text, text, text, text, text, text, boolean,
  text, text, text, timestamptz, jsonb
) to service_role;
grant execute on function public.record_outbound_message(
  bigint, bigint, text, text, text, jsonb
) to service_role;

-- Fase 2: resumo por conversa, lido junto com a janela de mensagens.

-- A janela ordena e corta por messages.id, que e a sequencia canonica
-- (occurred_at vem do provedor e pode empatar ou chegar fora de ordem).
create index if not exists messages_conversation_id_seq_idx
  on public.messages (conversation_id, id);

-- Necessario para a FK composta abaixo: o watermark de um resumo tem de
-- apontar para uma mensagem da propria conversa, nao de outra.
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.messages'::regclass
      and conname = 'messages_id_conversation_id_unico'
  ) then
    alter table public.messages
      add constraint messages_id_conversation_id_unico
      unique (id, conversation_id);
  end if;
end;
$$;

create table if not exists public.conversation_summaries (
  conversation_id bigint primary key
    references public.conversations(id) on delete cascade,
  summary text not null check (char_length(summary) <= 4000),
  covers_through_message_id bigint not null,
  covered_message_count integer not null default 0
    check (covered_message_count >= 0),
  model text not null,
  prompt_version text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint conversation_summaries_watermark_da_mesma_conversa
    foreign key (covers_through_message_id, conversation_id)
    references public.messages (id, conversation_id) on delete cascade
);

alter table public.conversation_summaries enable row level security;
revoke all on public.conversation_summaries from anon, authenticated;
grant select, insert, update on public.conversation_summaries to service_role;

create or replace function public.save_conversation_summary(
  p_conversation_id bigint,
  p_summary text,
  p_covers_through_message_id bigint,
  p_covered_message_count integer,
  p_model text,
  p_prompt_version text,
  p_expected_previous_message_id bigint
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
begin
  -- Concorrencia otimista. Duas mensagens da mesma conversa podem resumir
  -- ao mesmo tempo; a que partiu de um watermark ja superado perde e nao
  -- sobrescreve nada. O watermark nunca retrocede.
  if p_expected_previous_message_id is null then
    insert into public.conversation_summaries (
      conversation_id, summary, covers_through_message_id,
      covered_message_count, model, prompt_version
    )
    values (
      p_conversation_id, p_summary, p_covers_through_message_id,
      p_covered_message_count, p_model, p_prompt_version
    )
    on conflict (conversation_id) do nothing;
    return found;
  end if;

  update public.conversation_summaries as resumo
     set summary = p_summary,
         covers_through_message_id = p_covers_through_message_id,
         covered_message_count = p_covered_message_count,
         model = p_model,
         prompt_version = p_prompt_version,
         updated_at = now()
   where resumo.conversation_id = p_conversation_id
     and resumo.covers_through_message_id = p_expected_previous_message_id
     and p_covers_through_message_id > resumo.covers_through_message_id;

  return found;
end;
$$;

revoke all on function public.save_conversation_summary(
  bigint, text, bigint, integer, text, text, bigint
) from public, anon, authenticated;
grant execute on function public.save_conversation_summary(
  bigint, text, bigint, integer, text, text, bigint
) to service_role;


-- Fase 3: lancamentos extraidos dos documentos financeiros.

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

commit;
