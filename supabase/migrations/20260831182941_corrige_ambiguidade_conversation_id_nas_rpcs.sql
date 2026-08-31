-- `returns table (conversation_id ...)` cria uma variavel plpgsql com o mesmo
-- nome da coluna, e o `on conflict (conversation_id, ...)` fica ambiguo (42702).
-- A inferencia por colunas nao aceita qualificacao, entao apontamos a constraint
-- pelo nome -- e damos a ela um nome estavel em vez do autogerado.

do $$
begin
  if exists (
    select 1 from pg_constraint
    where conrelid = 'public.messages'::regclass
      and conname = 'messages_conversation_id_provider_message_id_key'
  ) then
    alter table public.messages
      rename constraint messages_conversation_id_provider_message_id_key
      to messages_provider_message_id_unico;
  end if;
end;
$$;

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
