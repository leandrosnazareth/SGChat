# Proposal

## Why

Hoje qualquer usuário autorizado pode consumir o modelo externo sem limite de tokens, de custo ou de frequência. O documento de software (§9) e o `prompt.txt` (seções 14–17) exigem cotas de tokens, budgets em US$, rate limits e registro de custos, com a opção de **redirecionar para a IA Local** em vez de bloquear quando um limite externo é atingido. Esta mudança implementa a **Fase 4** do roadmap (`prompt.txt`, seção 28: tokens, custos e cotas; `QuotaPolicy`; `BLOCK` e `LOCAL_FALLBACK`).

## What Changes

- **Cotas e budgets configuráveis** em `litellm/policies/quotas.yaml`: limites de tokens (`total_tokens`) e de custo (US$) por período (diário, semanal, mensal), aplicáveis a um usuário, aos membros de um grupo, a todos os usuários (cada um) ou ao total da empresa (global), por modelo ou para "todos os modelos externos". A IA Local não tem cota.
- **Ação ao exceder**, configurável por `TOKEN_QUOTA_ACTION` e `BUDGET_ACTION` (`LOCAL_FALLBACK` padrão, ou `BLOCK`):
  - `LOCAL_FALLBACK`: a requisição é atendida pela IA Local e o registro de uso grava `requested_model`, `effective_model` e `routing_reason` (`TOKEN_QUOTA_EXCEEDED` ou `BUDGET_EXCEEDED`);
  - `BLOCK`: HTTP 429 com o motivo.
- **Contabilização** a partir dos registros de uso do gateway (`prompt_tokens`, `completion_tokens`, `total_tokens`, custo), com o uso dos últimos segundos, ainda não gravado no banco, somado em memória.
- **Rate limit nativo do LiteLLM** por usuário: RPM e TPM padrão (`RATE_LIMIT_RPM=30`, `RATE_LIMIT_TPM=50000`) aplicados a todo usuário final, com HTTP 429 ao exceder.
- **Relatório de custos** por usuário, grupo, modelo, provedor e período (`scripts/usage-report.sh`). O grupo do usuário no momento da requisição passa a ficar gravado no registro de uso.
- Testes unitários e de fumaça (fallback, bloqueio, contabilização, rate limit) com consumo mínimo da cota do Gemini; documentação em `docs/quotas.md`.

### Fora do escopo

- Classificação de conteúdo e Security Router (Fases 5–6); modo AUTO (Fase 7). A ordem segurança → permissão → cota será revisada quando o Security Router existir.
- Rate limits diferentes por grupo (nesta fase o limite é o mesmo para todo usuário).
- Avisar o usuário, na interface, que a resposta veio da IA Local por causa de cota (fica registrado no gateway; aviso visual em mudança futura).
- Painéis no Langfuse (Fase 8).

### Impacto em segurança

O fallback **só redireciona para a IA Local**, nunca de um modelo local para um externo, e só se a política de acesso permitir a IA Local ao usuário; caso contrário, bloqueia. Se a contabilização estiver indisponível (banco inacessível) ou a política de cotas for inválida, chamadas a modelos externos recebem a ação configurada (fail-closed: na dúvida, não sai da empresa nem gera custo).

## Capabilities

### New Capabilities

- `usage-quotas`: cotas de tokens e budgets em US$ por usuário, grupo, todos os usuários ou global, por modelo e período; ações `LOCAL_FALLBACK`/`BLOCK`; registro do roteamento; contabilização e relatório de custos.
- `rate-limits`: limites de requisições e tokens por minuto por usuário final, aplicados pelo gateway.

### Modified Capabilities

(nenhuma — `model-access` e `ai-gateway` não mudam de comportamento)

## Impact

- **Novos**: `litellm/custom/quota_policy.py`, `litellm/policies/quotas.yaml`, `scripts/usage-report.sh`, `tests/unit/test_quota_policy.py`, `tests/smoke/quotas.sh`, `tests/smoke/rate-limit.sh`, `docs/quotas.md`.
- **Alterados**: `litellm/config.yaml` (callback, budget padrão de usuário final), `litellm/bootstrap.py` (cria/atualiza o budget de rate limit), `docker-compose.yml` e `.env.example` (variáveis `TOKEN_QUOTA_ACTION`, `BUDGET_ACTION`, `RATE_LIMIT_RPM`, `RATE_LIMIT_TPM`, `QUOTA_TIMEZONE`), `litellm/custom/model_access.py` (grava o grupo no registro de uso), `README.md`, `docs/architecture.md`, `docs/testing.md`.
- **Dependências**: nenhuma nova.
