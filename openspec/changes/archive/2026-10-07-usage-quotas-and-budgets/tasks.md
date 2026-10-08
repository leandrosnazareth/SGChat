# Tasks

## 1. Rate limit nativo

- [x] 1.1 Adicionar `RATE_LIMIT_RPM`, `RATE_LIMIT_TPM`, `TOKEN_QUOTA_ACTION`, `BUDGET_ACTION` e `QUOTA_TIMEZONE` ao `.env.example` (e ao `.env` local, via `scripts/init-env.sh`) e ao serviço `litellm`/`litellm-bootstrap` no compose; verificar com `docker compose config` que chegam aos containers
- [x] 1.2 Estender `litellm/bootstrap.py` para criar ou atualizar o budget `corporate-end-user-default` (rpm/tpm) de forma idempotente e configurar `max_end_user_budget_id` em `litellm/config.yaml`; verificar que o bootstrap roda duas vezes sem erro e que `/budget/info` mostra os limites
- [x] 1.3 Criar `tests/smoke/rate-limit.sh` (usuário único dispara RPM+1 requisições a `local-ai`; a excedente recebe 429; outro usuário recebe 200) e verificar que passa

## 2. Cotas e budgets

- [x] 2.1 Criar `litellm/policies/quotas.yaml` com as regras iniciais do D2 e verificar que é YAML válido
- [x] 2.2 Implementar `litellm/custom/quota_policy.py` (D2–D4: carga, validação e recarga da política; janelas de período; apuração banco + memória; fallback, bloqueio e fail-closed; `spend_logs_metadata`) e registrá-lo após `model_access` em `litellm/config.yaml`; verificar que o gateway sobe `healthy` e loga a política carregada
- [x] 2.3 Criar `tests/unit/test_quota_policy.py` (validação, períodos, regras aplicáveis, decisões, janela de memória, fail-closed) e verificar que todos passam no container
- [x] 2.4 Criar `tests/smoke/quotas.sh` (D6) e verificar que passa, com a política restaurada ao final (`git diff --exit-code litellm/policies`) e no máximo 1 chamada real ao Gemini

## 3. Relatório e validação no portal

- [x] 3.1 Criar `scripts/usage-report.sh` (agrupamentos por usuário, grupo, modelo e provedor; períodos dia, semana e mês) e verificar a saída com os dados gerados pelos testes
- [x] 3.2 Validar no navegador: com uma regra temporária de limite 0 para a Ana em `gemini`, a mensagem dela com `gemini` é respondida pela IA Local, e o registro de uso mostra `routing_reason=TOKEN_QUOTA_EXCEEDED`; restaurar a política. Registrar em `docs/testing.md`

## 4. Documentação e regressão

- [x] 4.1 Criar `docs/quotas.md` (regras, ações, períodos, rate limit, relatório, limitações) e atualizar `README.md` e `docs/architecture.md`; verificar seguindo `docs/quotas.md` para alterar um limite e observar o efeito sem reiniciar
- [x] 4.2 Rodar `scripts/healthcheck.sh`, todos os testes unitários e os smoke tests (`gateway.sh`, `model-access.sh`, `quotas.sh`, `rate-limit.sh`, `network-isolation.sh`); verificar que todos passam e registrar em `docs/testing.md`

## Workflow follow-up

- Arquivar com `/opsx:archive` após a validação; em seguida, planejar a Fase 5 (Security Router).
