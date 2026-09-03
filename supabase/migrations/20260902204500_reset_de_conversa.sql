-- Finbox AI - /reset: apagar o historico de uma conversa.
-- Idempotente: rodar de novo num projeto ja aplicado nao muda nada.
--
-- Existe porque o resumo rolling e texto livre do modelo e pode acumular
-- numero inventado. Uma vez contaminado, ele se realimenta a cada rodada.
-- Apagar e a saida; ate aqui so dava para fazer isso por fora, no SQL.

-- Ate agora nada apagava conversa, entao service_role nunca precisou de
-- delete. As tabelas filhas saem por cascade -- o Postgres nao checa
-- privilegio nelas --, mas conversations precisa do grant explicito.
grant delete on public.conversations to service_role;


create or replace function public.reset_conversation(
  p_conversation_id bigint
)
returns integer
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_total integer;
begin
  select count(*) into v_total
  from public.messages as message
  where message.conversation_id = p_conversation_id;

  -- Um delete so: messages, conversation_summaries e transactions saem
  -- por cascade. Deixar qualquer um deles para tras nao seria reset.
  delete from public.conversations as conversa
  where conversa.id = p_conversation_id;

  -- A linha da conversa se recria sozinha na proxima mensagem, dentro de
  -- record_inbound_message. O principal e a identidade ficam: eles dizem
  -- quem o usuario e, nao o que foi conversado.
  return v_total;
end;
$$;

revoke all on function public.reset_conversation(bigint)
  from public, anon, authenticated;

grant execute on function public.reset_conversation(bigint) to service_role;
