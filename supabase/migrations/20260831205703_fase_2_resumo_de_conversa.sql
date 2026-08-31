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
