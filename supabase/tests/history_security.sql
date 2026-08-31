begin;
select plan(12);

select ok(
  (select relrowsecurity from pg_class where oid = 'public.principals'::regclass),
  'principals has RLS enabled'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.principal_identities'::regclass),
  'principal_identities has RLS enabled'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.conversations'::regclass),
  'conversations has RLS enabled'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.messages'::regclass),
  'messages has RLS enabled'
);

select ok(
  not has_table_privilege('anon', 'public.messages', 'select'),
  'anon cannot read messages'
);
select ok(
  not has_table_privilege('anon', 'public.messages', 'insert'),
  'anon cannot insert messages'
);
select ok(
  not has_table_privilege('authenticated', 'public.messages', 'select'),
  'authenticated cannot read messages'
);
select ok(
  not has_table_privilege('authenticated', 'public.messages', 'insert'),
  'authenticated cannot insert messages'
);
select ok(
  has_table_privilege('service_role', 'public.messages', 'select'),
  'service_role can read messages'
);
select ok(
  has_table_privilege('service_role', 'public.messages', 'insert'),
  'service_role can insert messages'
);

select ok(
  exists (
    select 1 from pg_indexes
    where schemaname = 'public'
      and tablename = 'messages'
      and indexname = 'messages_conversation_timeline_idx'
  ),
  'conversation timeline index exists'
);
select ok(
  exists (
    select 1 from pg_indexes
    where schemaname = 'public'
      and tablename = 'messages'
      and indexname = 'messages_pending_idx'
  ),
  'pending messages partial index exists'
);

select * from finish();
rollback;
