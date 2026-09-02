# Contexto conversacional e remoção do guard

Status: proposta aprovada, não implementada
Fase: 0 — precede as fases 2 (perguntar sobre uma fatura) e 3 (comparar faturas)

## Problema atual

O fluxo de texto faz duas chamadas ao modelo: um classificador de domínio
(`classify_financial_topic`) e, se ele aprovar, a resposta. Quatro defeitos
confirmados em dados reais de produção:

1. **Falha do provedor vira recusa de escopo.** `except AIError: return False`
   em `ai.py` converte 429 de cota, timeout e erro de rede em "esse assunto
   está fora do escopo". No banco e no log, essa recusa é indistinguível de
   uma recusa por classificação — o que impede diagnosticar qual foi qual.
2. **Continuações são bloqueadas.** `"Sim quero ver"`, logo após o assistente
   oferecer uma separação de gastos, foi recusada. A rede de resgate
   (`_e_continuacao_financeira_inequivoca`) exige um termo financeiro
   explícito na própria mensagem, que uma continuação curta não tem.
3. **Recusas contaminam o histórico.** A recusa é gravada com
   `role: assistant` e volta na janela de contexto. O modelo a lê como
   exemplo do próprio comportamento e passa a imitá-la. Observado: quatro
   perguntas diferentes receberam deflexões idênticas, palavra por palavra.
4. **O guard decide com menos contexto do que quem responde.** Guard: 6
   mensagens. Resposta: 20 mensagens. Quem tem menos informação tem poder de
   veto sobre quem tem mais.

Os defeitos se encadeiam: um blip de rede vira recusa, a recusa é gravada,
o histórico ensina o modelo a recusar. O sistema piora sozinho.

## Decisão: remover o guard

O classificador separado deixa de existir. O escopo passa a ser
responsabilidade do prompt principal, na mesma chamada que responde.

A justificativa é que **o guard nunca foi controle de segurança**. Quem
controla acesso é a whitelist em `authorization.py`, determinística e
independente, que continua intacta. O guard sempre foi filtro de produto — e
um filtro de produto que erra para o lado restritivo causa mais dano que um
que erra para o permissivo, porque o usuário legítimo é quem paga.

Perde-se o gate determinístico: se o modelo desviar do escopo, nada barra.
Aceito conscientemente no cenário atual: agente pessoal, protegido por
allowlist e sem ferramentas capazes de executar ações financeiras ou outros
efeitos externos.

Esta decisão **deve ser revisitada antes de permitir ações comandadas pelo
modelo**, como criar lembretes, enviar notificações, alterar lançamentos ou
realizar operações financeiras. A allowlist controla quem acessa o sistema;
ela não valida conteúdo nem torna segura uma ação sugerida pelo modelo.

## Novo fluxo

Antes:

```
build_context()  →  20 msgs + resumo
    ├─ classify_financial_topic(6 msgs, resumo)   → false → string fixa
    └─ answer_financial_question(20 msgs, resumo)
```

Depois:

```
build_context()  →  20 msgs + resumo
    └─ answer_financial_question(20 msgs, resumo)   ← única chamada de resposta
                                                        ↓
                              maybe_refresh_summary()   ← chamada eventual,
                                                         após a resposta
```

Cada mensagem de texto faz exatamente uma chamada destinada a responder ao
usuário e nenhuma chamada de classificação. A atualização rolling do resumo
pode fazer uma chamada adicional de manutenção quando atingir o limiar de
`SUMMARY_EVERY`; ela ocorre depois da resposta e não faz parte da chamada de
resposta.

O custo das interações simples cai aproximadamente pela metade. No plano
gratuito sem créditos, o OpenRouter informa atualmente um teto total de 50
requisições diárias para modelos gratuitos. Isso representa **até cerca de
50 interações simples**, não uma garantia: resumo rolling, documentos,
falhas e novas tentativas também consomem requisições. Esse limite é uma
condição operacional externa e pode mudar; não é uma premissa arquitetural.

Referência operacional:
<https://openrouter.ai/docs/faq>

## Responsabilidades

| Camada | Responsabilidade |
|---|---|
| **Prompt** | interpretar o contexto; reconhecer continuações curtas; decidir conversacionalmente se o assunto é alheio a finanças; redigir a recusa de forma natural e variada; pedir esclarecimento quando ambíguo; não prometer ações inexistentes; não inventar valores financeiros |
| **Backend** | whitelist e autorização; comandos (`/ativar`, `/desativar`); janela e resumo de contexto; cálculos; validação e persistência; efeitos externos; tratamento e classificação técnica de erros |

A divisão preserva o princípio central do projeto: **o modelo interpreta e
redige; o Python controla autorização, cálculos, persistência e efeitos
externos**. Escopo conversacional pode ir para o prompt. Soma, autorização,
persistência e ações não podem depender apenas do modelo.

Essa separação forma uma interface mais profunda: `_processar_com_ia` entrega
contexto completo a uma única operação de resposta, e a implementação dessa
operação concentra interpretação, continuidade e redação sem expor um
segundo classificador aos chamadores.

## Falhas do provedor

`AIError` (429, timeout, resposta ilegível, rede) passa a resultar em
`INDISPONIVEL` — "não consegui processar agora, tente de novo em instantes".
Nunca em recusa de escopo.

O motivo é que a mensagem precisa ser verdadeira: dizer "isso está fora do
escopo" quando na verdade a cota acabou desinforma o usuário e apaga o rastro
do problema real. A distinção também precisa sobreviver no log — uma falha de
provedor deve aparecer como falha, não como decisão de produto.

`INDISPONIVEL` pode continuar sendo uma mensagem operacional fixa. A
proibição de string fixa se aplica às recusas de domínio do fluxo ativo, não
às mensagens determinísticas de erro técnico.

## Assuntos fora do escopo

Recusa gerada pelo modelo, na mesma chamada, com o contexto à vista. Curta,
natural, variando conforme a conversa, e redirecionando para finanças.
Nenhuma string fixa participa do fluxo ativo de recusa.

Só recusar quando o assunto for **inequivocamente** alheio — o exemplo de
referência é "a vaca bebe leite ou água?". Em caso de dúvida, a ordem de
preferência é: interpretar pelo histórico, pedir esclarecimento, e só então
recusar.

Uma recusa de domínio produzida pelo novo prompt é uma mensagem que o usuário
realmente recebeu e pode permanecer no histórico. Ela não deve ser tratada
como regra para a próxima mensagem: o prompt deve sempre reavaliar a
continuidade com base no contexto atual. Se recusas futuras voltarem a gerar
um ciclo de imitação, a evolução indicada é fazer a mesma chamada retornar
também um tipo estruturado, como `answer`, `clarification` ou
`domain_redirect`, sem recriar um segundo classificador.

## Compatibilidade com recusas legadas

A recusa fixa já gravada antes desta mudança não é uma resposta confiável de
domínio, pois também pode representar falha do provedor. Ela deve ser
preservada em `app/history.py` como constante de compatibilidade e excluída
do contexto por igualdade exata após normalização de espaços.

A exclusão precisa acontecer nos dois adapters do histórico:

- no adapter em memória, por `is_context_eligible`;
- no adapter do Supabase, no filtro enviado ao banco **antes do `LIMIT`**.

No Supabase, a exclusão vale tanto para `recent_eligible`, que monta a janela,
quanto para `eligible_after`, que alimenta o resumo incremental. Filtrar só
depois da consulta permitiria que recusas ocupassem vagas da janela e
empurrassem mensagens úteis para fora.

Resumos já persistidos podem conter a recusa legada mesmo depois de as
mensagens serem filtradas. Sem alterar o banco, a normalização de
compatibilidade deve remover essa frase sempre que um resumo for carregado:
tanto ao montar o contexto quanto ao usar o resumo anterior na atualização
rolling. Assim, o texto contaminado não chega à resposta nem ao próximo
resumo. Novos resumos não recebem a recusa porque `eligible_after` também a
exclui.

## Valores financeiros e proveniência

Os totais e valores derivados pelo fluxo de documentos continuam sendo
calculados pelo Python. O prompt principal deve proibir a criação de valores
financeiros ausentes das mensagens, do resumo ou de dados confiáveis
fornecidos pelo backend.

Nesta fase, essa proteção no fluxo livre de conversa é uma instrução ao
modelo, não uma garantia matemática do backend. Portanto, a afirmação
`todo número exibido vem do Python` aplica-se aos totais e valores derivados
de documentos, e não a qualquer número que possa aparecer em texto livre.
Se a proveniência de todo número financeiro precisar ser garantida, será
necessário introduzir posteriormente saída estruturada e validação
determinística no backend.

## Conversa e criação de lembretes

Distinção que precisa ficar registrada agora, embora o subsistema de
lembretes seja a fase 4 e não exista ainda:

**Conversar sobre um lembrete é redação. Criar um lembrete é uma ação de
backend.** O modelo pode entender a intenção e explicar a capacidade atual;
nada passa a existir sem validação, persistência e confirmação determinísticas.

Risco concreto que esta seção previne: com uma chamada livre, o modelo pode
responder "certo, te lembro dia 10" sem que nada seja agendado — uma promessa
que o sistema não cumpre. Enquanto a fase 4 não existir, **o prompt deve
declarar que o Finbox ainda não cria lembretes**, em vez de deixar o modelo
improvisar.

A mesma regra vale para qualquer efeito externo: o modelo nunca pode afirmar
que criou, alterou, enviou, agendou, pagou ou executou algo sem receber do
backend uma confirmação explícita de sucesso.

## Configuração removida

Como o classificador deixa de existir, `OPENROUTER_MODEL_GUARD` e
`GUARD_WINDOW` deixam de fazer parte da interface de configuração. Devem ser
removidos integralmente de:

- `app/config.py`, incluindo variáveis, `OBRIGATORIAS` e `valores_atuais`;
- `.env.example`;
- variáveis configuradas no Coolify;
- fixtures e testes.

Não serão mantidos como opções depreciadas porque não há consumidor legítimo
para eles depois da remoção do guard.

## Arquivos afetados

| Arquivo | Mudança |
|---|---|
| `app/ai.py` | remove `PROMPT_GUARD`, `classify_financial_topic`, `_ler_booleano`, `_TERMOS_FINANCEIROS_EXPLICITOS`, `_INICIOS_DE_CONTINUACAO`, `_e_continuacao_financeira_inequivoca`; reescreve `PROMPT_RESPOSTA` com escopo, continuações, proveniência de valores e a ressalva de ações inexistentes |
| `app/main.py` | `_processar_com_ia` perde o ramo do guard; remove `FORA_DO_DOMINIO`; preserva tratamento separado de `AIError` |
| `app/memory.py` | `guard_messages` e `guard_window` saem de `ContextBundle` e `ConversationMemory`; aplica a normalização da recusa legada ao resumo usado na resposta e na atualização rolling |
| `app/config.py` | remove integralmente `OPENROUTER_MODEL_GUARD` e `GUARD_WINDOW` |
| `.env.example` | remove `OPENROUTER_MODEL_GUARD` e `GUARD_WINDOW` |
| `app/history.py` | preserva a recusa fixa exclusivamente como constante de compatibilidade; exclui-a da elegibilidade e centraliza a normalização de resumos legados |
| `app/adapters/supabase_history_adapter.py` | exclui a recusa legada antes do `LIMIT` em `recent_eligible` e `eligible_after` |
| configuração do Coolify | remove as duas variáveis sem uso; nenhuma mudança de infraestrutura ou réplica é necessária |
| testes | atualiza `test_ai.py`, `test_fluxo_webhook.py`, `test_contexto_webhook.py`, `test_memory.py`, `test_history.py`, testes do adapter Supabase e `test_config_validacao.py` |

Sem migração. Nenhum schema, tabela, RPC ou outro objeto do banco muda. A
compatibilidade com resumos já contaminados é feita em tempo de leitura.

## Critérios de aceite

Automáticos, com o modelo mockado:

1. cada mensagem de texto invoca exatamente uma operação
   `answer_financial_question` e nenhuma operação de classificação;
2. uma chamada adicional de `summarize_conversation` só pode ocorrer quando
   `SUMMARY_EVERY` for atingido, depois de a resposta ter sido enviada e
   persistida, e não conta como chamada de resposta;
3. a chamada de resposta recebe a janela completa configurada — até 20
   mensagens, sujeita a `HISTORY_MAX_CHARS` — e o resumo, não uma janela
   reduzida de guard;
4. `AIError` produz `INDISPONIVEL`; nenhum tratamento de erro técnico produz
   recusa de domínio;
5. nenhuma string fixa de escopo participa do fluxo ativo de resposta; a
   constante legada existe apenas na elegibilidade e normalização histórica;
6. uma recusa legada não entra em `recent_eligible` nem em `eligible_after`,
   tanto no adapter em memória quanto no Supabase, enquanto uma resposta
   comum do assistente continua elegível;
7. a filtragem do Supabase ocorre antes do `LIMIT`, sem reduzir artificialmente
   a janela útil;
8. um resumo legado contaminado é normalizado antes de chegar ao prompt de
   resposta e antes de alimentar o próximo resumo rolling;
9. `OPENROUTER_MODEL_GUARD` e `GUARD_WINDOW` não fazem mais parte da
   configuração nem dos testes;
10. o prompt proíbe valores financeiros sem fonte no contexto e proíbe a
    promessa de lembretes ou outros efeitos externos não confirmados;
11. o fluxo de documento e a persistência de lançamentos seguem intactos.

O critério 1 impede a implementação de `resolver o sim, quero ver` mantendo
o problema estrutural: enquanto houver duas chamadas de decisão/resposta, o
classificador separado ainda existe sob outro nome. O resumo rolling é uma
operação de manutenção independente e explicitamente excluída dessa contagem.

Smoke com modelo real, manual como os testes `.sql`, porque consome cota e
não é determinístico:

12. continuidade: `quanto gastei com alimentação?` → `sim, quero ver` → responde;
13. ambíguo: `e no mês passado?` → responde ou pede esclarecimento, nunca recusa;
14. fora de escopo: `a vaca bebe leite ou água?` → recusa curta, natural e redireciona;
15. lembrete: `me lembra de pagar dia 10` → diz que ainda não faz isso, sem prometer;
16. repetir conversas equivalentes não deve revelar uma deflexão fixa imposta
    pelo backend. Variação textual do modelo é observação qualitativa, não
    bloqueio determinístico de release.

## O que não será alterado

- **Banco de dados**: nenhum schema, tabela, RPC ou migração;
- **Autorização e whitelist**: `authorization.py` intacto, incluindo a regra
  de grupo e as identidades por telefone e LID;
- **Comandos**: `/ativar` e `/desativar` continuam determinísticos e fora do
  alcance do modelo;
- **Fluxo de documento**: extração, `somar()`, `resumir()` e a resposta à
  legenda seguem como estão;
- **Persistência de lançamentos** (fase 1): intacta;
- **Aritmética de documentos**: totais e valores derivados continuam vindo
  do Python;
- **Resumo rolling**: permanece como manutenção best-effort após a resposta;
- **Mensagem operacional `INDISPONIVEL`**: permanece determinística e distinta
  de qualquer recusa de domínio.
