# Design

## Context

Estado atual: o gateway (LiteLLM v1.104.0) identifica o usuário final pelo e-mail (`end_user_id`) e aplica a política de acesso (`litellm/custom/model_access.py`). Cada requisição gera uma linha em `LiteLLM_SpendLogs` (`end_user`, `model_group`, `custom_llm_provider`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `spend`, `status`, `startTime`, `metadata`).

Investigação (07/10/2026, LiteLLM v1.104.0):
- **Rate limit nativo por usuário final**: `UserAPIKeyAuth.end_user_rpm_limit`/`end_user_tpm_limit` são aplicados pelos limitadores (`parallel_request_limiter.py` e `parallel_request_limiter_v3.py`). Para usuários finais **não cadastrados** no banco do LiteLLM, `user_api_key_auth.py` aplica o budget padrão `litellm.max_end_user_budget_id` (linhas ~1915–1930). Budgets são criados via `POST /budget/new` (`budget_id`, `rpm_limit`, `tpm_limit`, …).
- **Budgets nativos** (`max_budget`) só bloqueiam: não fazem fallback para outro modelo nem contam tokens por modelo/período. Por isso cotas e budgets em US$ são próprios.
- **Redirecionamento**: teste em container descartável. Alterar `data["model"]` no `async_pre_call_hook` faz a chamada (normal e streaming) ser atendida pelo outro modelo; o registro de uso fica com `model_group` do modelo efetivo, e `data["metadata"]["spend_logs_metadata"]` é preservado no evento de sucesso. A resposta mantém no campo `model` o nome pedido pelo cliente.
- **Atraso de gravação**: os registros de uso são gravados em lote a cada `PROXY_BATCH_WRITE_AT` = 10 s.
- O evento `async_log_success_event` traz `standard_logging_object` com `end_user`, `model_group`, `total_tokens`, `prompt_tokens`, `completion_tokens` e `response_cost`.

## Goals / Non-Goals

**Goals:** cotas e budgets por regra, com fallback para a IA Local e registro do motivo; rate limit nativo; relatório de custos; testes que gastem no máximo 1 requisição do Gemini por execução.

**Non-Goals:** rate limit por grupo; aviso na interface quando houve fallback; painéis.

## Decisions

### D1. Rate limit = budget padrão nativo de usuário final
- `litellm-bootstrap` cria ou atualiza o budget `corporate-end-user-default` com `rpm_limit=$RATE_LIMIT_RPM` e `tpm_limit=$RATE_LIMIT_TPM`, sem `max_budget`.
- `litellm_settings.max_end_user_budget_id: corporate-end-user-default` **e** `metadata.end_user_budget_id` na chave do portal. Constatado na implementação: com chave virtual (o caso do portal), `user_api_key_auth.py` só aplica o budget padrão de usuário final quando a própria chave declara `end_user_budget_id` (`get_key_end_user_budget_id`). O ajuste global cobre os demais caminhos de autenticação. Sem o metadado da chave, 31 requisições seguidas passaram; com ele, a 31ª recebeu 429.
- O LiteLLM recusa o excedente com HTTP 429. O limite vale para todos os modelos, inclusive a IA Local, porque protege a infraestrutura.
- *Alternativa*: contadores próprios no hook. Rejeitada: o `prompt.txt` pede preferir o nativo.
- O valor do budget pode ficar em cache no gateway por alguns minutos após uma mudança. Para aplicar na hora, reinicia-se o `litellm`.

### D2. Política de cotas (`litellm/policies/quotas.yaml`)

```yaml
version: 1
fallback_model: local-ai          # destino do LOCAL_FALLBACK
unlimited_models: [local-ai]      # nunca limitados
rules:
  - name: externo-mensal-por-usuario
    applies_to: all_users         # user:<email> | group:<NOME> | all_users | global
    models: external              # lista de modelos, ou "external" (todos menos os ilimitados)
    period: monthly               # daily | weekly | monthly
    max_tokens: 1000000           # opcional
    max_cost_usd: 20              # opcional (pelo menos um dos dois)
    action: BLOCK                 # opcional: sobrepõe TOKEN_QUOTA_ACTION/BUDGET_ACTION
```

- Regras iniciais, seguindo os exemplos do `prompt.txt`:
  - externo mensal por usuário: 1.000.000 tokens / US$ 20;
  - `gemini` mensal por usuário: 700.000 tokens;
  - global externo mensal: US$ 200.
- `user:` e `group:` comparam com o e-mail normalizado e os grupos da política de acesso (`model-access.yaml`). `all_users` e `user:`/`group:` medem o consumo **do próprio usuário**; `global` mede o total de todos.
- Todas as regras aplicáveis valem (a mais restritiva vence).
- Período em calendário no fuso `QUOTA_TIMEZONE` (padrão `America/Sao_Paulo`): dia corrente; semana a partir de segunda-feira; mês corrente.
- Ações padrão por variável de ambiente (`TOKEN_QUOTA_ACTION`, `BUDGET_ACTION`; padrão `LOCAL_FALLBACK`); `action` na regra sobrepõe. Isso também permite testar `BLOCK` sem reiniciar.
- Validação no carregamento e recarga por mtime (mesmo padrão do `model_access`); inválida → fail-closed.
- Uma regra com limite **0** já começa esgotada. Os testes usam isso para provocar fallback e bloqueio sem consumir a cota do Gemini.

### D3. Hook `custom/quota_policy.py` (`QuotaPolicy`, CustomLogger)
- Registrado **depois** de `model_access` em `litellm_settings.callbacks` (permissão antes de cota).
- `async_pre_call_hook`:
  - chave mestra, modelo ilimitado ou sem `end_user_id` → segue (a ACL já negou quem não tem identidade);
  - caso contrário, apura o consumo de cada regra aplicável;
  - se excedida: `LOCAL_FALLBACK` → `data["model"] = fallback_model`; `BLOCK` → `HTTPException(429, {"error": "TOKEN_QUOTA_EXCEEDED"|"BUDGET_EXCEEDED", ...})`.
  - Se tokens e custo estiverem excedidos ao mesmo tempo, o motivo é `TOKEN_QUOTA_EXCEEDED`.
- Grava em `data["metadata"]["spend_logs_metadata"]`: `requested_model`, `effective_model`, `routing_reason` (`DIRECT`, `TOKEN_QUOTA_EXCEEDED`, `BUDGET_EXCEEDED`, `QUOTA_CHECK_UNAVAILABLE`), `quota_rule` e `groups` (os grupos do usuário no momento, usados pelo relatório).
- Para saber se o fallback é permitido e quais são os grupos, carrega a política de acesso pelo mesmo módulo `model_access.py`, importado por caminho de arquivo, e consulta a política vigente.

### D4. Apuração do consumo
- Banco: `prisma_client.db.query_raw` (cliente do próprio proxy) somando `total_tokens` e `spend` de `LiteLLM_SpendLogs` com `status='success'`, filtrado por usuário (`lower(end_user)`), `model_group` e janela `[início do período, agora − 30 s)`.
- Memória: `async_log_success_event` guarda (instante, usuário, modelo, tokens, custo) dos últimos 120 s e soma os eventos com instante ≥ agora − 30 s. Isso cobre o atraso de gravação (10 s, com folga) sem contar em dobro.
- Cache de 3 s por (regra, usuário) para não consultar o banco várias vezes na mesma rajada.
- Falha na consulta → `QUOTA_CHECK_UNAVAILABLE` → ação configurada (fail-closed).

### D5. Relatório (`scripts/usage-report.sh`)
SQL sobre `LiteLLM_SpendLogs` (`status='success'`) com período (`--period day|week|month`, padrão `month`) e agrupamento (`--by user|group|model|provider`). O grupo vem de `metadata->spend_logs_metadata->groups` e é `(sem registro)` para linhas anteriores a esta mudança.

### D6. Testes
- **Unitários** (`tests/unit/test_quota_policy.py`, executados no container): validação da política, janelas de período, regras aplicáveis, decisão (fallback/bloqueio/fail-closed) com apuração simulada e janela de memória.
- **`tests/smoke/quotas.sh`** contra o gateway real, com política temporária restaurada via `trap`:
  - limite 0 em `gemini` → fallback com `routing_reason=TOKEN_QUOTA_EXCEEDED` (verificado no registro de uso);
  - `max_cost_usd: 0` → `BUDGET_EXCEEDED`; `action: BLOCK` → 429;
  - `local-ai` nunca limitado; política inválida → fail-closed;
  - contabilização com **1** chamada real ao `gemini`: depois de uma regra com limite menor que o consumo, a requisição seguinte já é redirecionada.
- **`tests/smoke/rate-limit.sh`**: usuário de teste dispara `RATE_LIMIT_RPM + 1` requisições a `local-ai` (`max_tokens` 1) e a última recebe 429; outro usuário continua 200.

## Risks / Trade-offs

- **Gateway com várias réplicas** quebraria a janela em memória → POC com uma réplica; documentado (produção: Redis).
- **Custo do Gemini estimado pelo LiteLLM** (tabela de preços dele), não pela fatura do Google → documentado.
- **O campo `model` da resposta mostra o modelo pedido** mesmo em fallback → o registro de uso mostra o efetivo; aviso visual fica para o futuro.
- **Rate limit também atinge a IA Local** → intencional (proteção de capacidade); RPM 30 é folgado para uso humano.
- **Consulta ao banco a cada chamada externa** → limitada pelo cache de 3 s; índices existentes em `end_user` e `startTime`.

## Migration Plan

Atualizar config, compose e `.env` (variáveis novas com padrão), rodar `docker compose up -d litellm litellm-bootstrap`. Rollback: remover `custom.quota_policy.handler` dos callbacks e `max_end_user_budget_id` do config.
