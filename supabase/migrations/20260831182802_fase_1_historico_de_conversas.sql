-- Finbox AI - esquema completo: estado do bot e fase 1 do historico.
-- Aplicada em 2026-08-31 via migracao registrada.
-- Tudo aqui e idempotente: rodar de novo num projeto ja aplicado nao muda nada.


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
  on conflict (conversation_id, provider_message_id) do nothing
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
  on conflict (conversation_id, provider_message_id) do nothing
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

