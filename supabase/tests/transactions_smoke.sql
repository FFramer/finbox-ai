-- Exercita record_document_transactions contra o banco real e desfaz tudo.
-- Cole no SQL Editor. Nada aqui persiste: o rollback fecha a transacao.
--
-- A suite Python simula a camada HTTP, entao nenhum teste de la executa o
-- plpgsql desta funcao. Erro de coluna ambigua, cast de data ou grant so
-- aparece aqui.
--
-- Esperado:
--   a_gravou              total = 3
--   b_linhas              3 linhas, na ordem do documento
--   c_data_invalida       occurred_on nulo, resto preenchido
--   d_reprocessou         total = 3 (substituiu, nao duplicou)
--   e_total_apos_reenvio  3 linhas, nunca 6
--   f_soma                418.32

begin;

create temp table smoke_doc (conversation_id bigint, message_id bigint, created boolean);

insert into smoke_doc
select * from public.record_inbound_message(
  'primary', 'whatsapp', 'finbox', '5599@s.whatsapp.net', '5599@s.whatsapp.net',
  'phone', 'Smoke', false, 'SMOKE-DOC-1', 'document', null, now(),
  '{"document": {"name": "fatura.pdf", "mimetype": "application/pdf"}}'::jsonb);

-- A terceira linha traz occurred_on nulo de proposito: e o caso em que o
-- modelo devolveu "02 AGO" e o Python nao conseguiu converter para ISO.
create temp table smoke_payload (linhas jsonb);
insert into smoke_payload values ('[
  {"position": 1, "occurred_on": "2026-08-02", "description": "SUPERMERCADO ZONA SUL",
   "amount": "186.42", "category": "Mercado"},
  {"position": 2, "occurred_on": "2026-08-03", "description": "UBER *TRIP",
   "amount": "27.90", "category": "Transporte"},
  {"position": 3, "occurred_on": null, "description": "PADARIA REAL",
   "amount": "204.00", "category": "Alimentacao"}
]'::jsonb);

select 'a_gravou' as etapa,
       public.record_document_transactions(
         (select message_id from smoke_doc), (select linhas from smoke_payload)
       ) as total;

select 'b_linhas' as etapa, position, occurred_on, description, amount, category
from public.transactions
where message_id = (select message_id from smoke_doc)
order by position;

select 'c_data_invalida' as etapa, position, occurred_on, description
from public.transactions
where message_id = (select message_id from smoke_doc) and occurred_on is null;

-- Reenvio do mesmo documento: a Evolution repete o evento apos um timeout.
select 'd_reprocessou' as etapa,
       public.record_document_transactions(
         (select message_id from smoke_doc), (select linhas from smoke_payload)
       ) as total;

select 'e_total_apos_reenvio' as etapa, count(*) as linhas
from public.transactions
where message_id = (select message_id from smoke_doc);

-- A soma sai do banco, nunca do modelo: e o mesmo principio do somar() em
-- Python, agora valendo para agregacao entre documentos.
select 'f_soma' as etapa, sum(amount) as total
from public.transactions
where message_id = (select message_id from smoke_doc);

-- Mensagem inexistente tem que falhar alto, nao gravar orfao.
-- Descomente para conferir; aborta a transacao, entao rode por ultimo.
-- select public.record_document_transactions(-1, '[]'::jsonb);

rollback;
