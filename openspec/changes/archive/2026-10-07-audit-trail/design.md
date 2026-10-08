# Design

## Context

- **Langfuse 4.53.0 self-hosted**: web, worker, Postgres, ClickHouse, Redis e MinIO.
  - Projeto `corporate-ai-platform` e chaves criados pelas variáveis `LANGFUSE_INIT_*`.
  - Roda em modo **v4 events_only**: `/api/public/traces` não existe. A consulta é por `/api/public/v2/observations`, com filtros por `userId`, `sessionId`, tags e metadados (`filter` em JSON).
- **LiteLLM v1.104.0**: callback nativo `langfuse` com SDK Langfuse 4.15.2, que envia por OTLP para `/api/public/otel`.
  - Usa o usuário final como `userId` e o cabeçalho `x-litellm-session-id` (ID da conversa) como sessão.
  - Cria um trace por chamada.
  - Grava `spend_logs_metadata` aninhado nos metadados da observação.

### Protótipo (07/10/2026)

O que o callback nativo fez sem nenhuma adaptação:
- **Funcionou**: usuário, sessão, entrada e saída, tokens, custo (`gemini`: US$ 0,000008), latência e streaming (um registro, com uso).
- **Falha de nível `ERROR`** para bloqueios dos hooks.
- **Problema 1**: a senha de um bloqueio `RESTRICTED` foi gravada em `input` e também em `modelParameters.messages`.
- **Problema 2**: decisões só aninhadas em `spend_logs_metadata` e sem tags próprias.
- **Problema 3**: requisição negada pela ACL sem nenhuma decisão.
- **Problema 4**: bloqueio por cota mostrando `effective_model=gemini`.
- **Problema 5**: falhas geradas no próprio gateway aparecem **duas vezes** no mesmo trace. Os dois tratadores de falha do LiteLLM (assíncrono e síncrono em thread) chamam o Langfuse; o mesmo ocorre registrando o callback em `callbacks`.
- **Langfuse parado**: o gateway respondeu normalmente (≈0,2 s). O SDK tenta reenviar; 1 de 2 traces chegou depois da volta.

## Goals / Non-Goals

**Goals:**
- trace completo para toda chamada, inclusive bloqueios;
- decisões pesquisáveis;
- segredos fora do Langfuse;
- registro imune a manipulação pelo cliente;
- gateway independente do Langfuse.

**Non-Goals:**
- papéis de auditor e trilha de acesso (Fase 9);
- retenção;
- entrega garantida (Fase 11);
- deduplicar o evento duplo do LiteLLM (exigiria alterar o LiteLLM).

## Decisions

### D1. Callback nativo, não SDK próprio
`success_callback` e `failure_callback` com `langfuse`, mais `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY` e `LANGFUSE_SECRET_KEY` no container do gateway, com os mesmos valores de `LANGFUSE_INIT_PROJECT_*` do `.env`. O callback já cobre conteúdo, consumo, erros e streaming; código próprio só para o que é nosso.

### D2. `audit_metadata.start` (primeiro hook)
- **Remove do pedido**:
  - no corpo: `turn_off_message_logging` e `langfuse_*`;
  - em `metadata`: `tags`, `mask_input`, `mask_output`, `existing_trace_id`, `generation_name`, `generation_id`, `parent_observation_id`, `debug_langfuse`, `langfuse_masking_function` e qualquer `trace_*`, exceto o `trace_id` que o próprio LiteLLM define igual ao `session_id`;
  - em `metadata.headers`: os cabeçalhos de redação.
  - O que foi removido vai para o log.
- `metadata.trace_metadata` = **a mesma referência** de `spend_logs_metadata`. Os hooks seguintes preenchem esse dicionário, e o callback lê o estado final na hora do registro, inclusive quando um hook lança exceção. As chaves ficam no nível de cima dos metadados da observação, onde o filtro do Langfuse alcança.
- `trace_name=corporate-ai-chat` e `audit_version=1`.

O LiteLLM já impede, sem configuração, desligar callbacks por cabeçalho (`x-litellm-disable-callbacks`, recurso premium) e trocar o `langfuse_host` por requisição. Ainda assim o hook remove essas chaves, e o smoke test verifica os dois casos.

### D3. `audit_metadata.finish` (último hook)
`metadata.tags` = `classification:<X>`, `requested:<m>`, `effective:<m>`, `routing:<motivo>` e `content:redacted` quando houver. Só vale para requisições que passaram por todos os hooks; bloqueios são encontrados por metadados (`blocked=true`, `routing_reason`) e pelo nível `ERROR`.

### D4. Decisão registrada em bloqueios
Antes de lançar a exceção:
- `model_access`: `requested_model`, `routing_reason=MODEL_ACCESS_DENIED`, `access_reason` e `blocked=true`;
- `security_router`, `quota_policy` (`BLOCK`) e `model_router` (sem candidato): `blocked=true` e remoção de `effective_model`.

### D5. Redação de `RESTRICTED`
No bloqueio por `RESTRICTED`, o Security Router troca o `content` de todas as mensagens (e `prompt` e `input`) por `[REDACTED: RESTRICTED — <regras>]` e marca `content_redacted=true`. O LiteLLM monta o registro da falha a partir desse mesmo dicionário, o que cobre `input` e `modelParameters`. Conteúdo `CONFIDENTIAL` e `INTERNAL` é gravado integralmente: o auditor precisa reconstruir a conversa (seção 19).

### D6. Busca
`scripts/audit-search.sh` monta o filtro JSON de `/api/public/v2/observations`:

| Opção | Filtro |
|---|---|
| `--user` | `userId` |
| `--conversation` | `sessionId` |
| `--from`, `--to` | período |
| `--classification` | metadado |
| `--requested`, `--effective` | metadados |
| `--reason` | metadado |
| `--blocked` | metadado |
| `--level` | nível |

A saída é uma tabela com hora, usuário, conversa, modelo pedido → efetivo, classificação, motivo, tokens, custo e status. Com `--show`, imprime a entrada e a saída. Usa as chaves do projeto do `.env`; é ferramenta de operação, não de usuário final, e a Fase 9 cuida do acesso de auditores.

### D7. Testes
- **Unitários** (`test_audit_metadata.py`):
  - `sanitize` (cada chave, cabeçalhos, `trace_id` do LiteLLM preservado);
  - tags;
  - referência compartilhada (alteração posterior aparece em `trace_metadata`);
  - `redact_messages`.
- **Smoke** (`tests/smoke/audit.sh`, gateway e Langfuse reais, só IA Local e bloqueios, nenhuma chamada ao Google), um usuário por execução:
  - chamada atendida (trace com usuário, sessão, entrada, saída, tokens e latência);
  - confidencial redirecionado (decisões e tags);
  - senha bloqueada (redação em `input` e `modelParameters`, `blocked`);
  - ACL negada;
  - tentativa de mascarar e reetiquetar;
  - tentativa de desligar o callback e de trocar o host;
  - buscas por cada campo;
  - Langfuse parado: gateway responde e o log registra a falha; Langfuse religado.
- **Navegador**: trace na interface do Langfuse (Teste 8 do `prompt.txt`) e filtros por tag e metadado.

## Risks / Trade-offs

- **Evento duplicado em falhas do gateway** → mesmo trace, dois registros. A contagem por trace continua correta; documentado. A deduplicação fica para quando o LiteLLM corrigir.
- **Perda de traces com o Langfuse fora do ar por mais tempo que as tentativas do SDK** → `LiteLLM_SpendLogs` mantém a decisão sem o conteúdo. Avaliar bloqueio ou fila na Fase 11.
- **Conteúdo confidencial no Langfuse** → é o objetivo da auditoria. Langfuse em rede interna, acesso por login; a retenção fica para a Fase 9/10.
- **Volume** → cada chamada do portal, inclusive a geração de títulos, gera um trace. Aceitável na POC.

## Migration Plan

Adicionar as variáveis e os callbacks, reiniciar o `litellm`. Rollback: remover os callbacks `langfuse` e os dois hooks `audit_metadata` do `config.yaml`.
