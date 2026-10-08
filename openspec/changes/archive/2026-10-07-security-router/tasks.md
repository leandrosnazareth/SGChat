# Tasks

## 1. Política e detectores

- [x] 1.1 Criar `litellm/policies/security.yaml` com ações, Presidio, regex, palavras-chave, entidades classificadas e domínios internos do D2–D3; verificar que é YAML válido e que todas as regex compilam
- [x] 1.2 Implementar `litellm/custom/security_router.py` (carga, validação e recarga da política; detectores com validadores de CPF/CNPJ; normalização; Presidio com timeout; extração de contexto; cache por mensagem; consolidação; decisão; metadados; fail-closed) conforme D2–D6; verificar que o módulo importa no container `litellm`
- [x] 1.3 Criar `tests/unit/test_security_router.py` (D7) e verificar que todos passam no container

## 2. Integração no gateway

- [x] 2.1 Ajustar `litellm/custom/quota_policy.py` para não sobrescrever `requested_model`/`routing_reason` já gravados, e rodar de novo `tests/unit/test_quota_policy.py`; verificar que todos passam
- [x] 2.2 Registrar `custom.security_router.handler` entre `model_access` e `quota_policy` em `litellm/config.yaml` e `SECURITY_POLICY_PATH` no compose; reiniciar o `litellm` e verificar no log a política carregada e o gateway `healthy`

## 3. Validação

- [x] 3.1 Criar `tests/smoke/security.sh` (D7, incluindo o teste 10 com `local-ai` parado e restaurado) e verificar que passa, com a política restaurada e no máximo 1 chamada real ao Gemini
- [x] 3.2 Validar no navegador: Ana escolhe `gemini` e envia "Analise o contrato confidencial do cliente XPTO." → resposta da IA Local e registro com `SECURITY_POLICY`; mensagem com senha → erro de bloqueio exibido. Registrar em `docs/testing.md`

## 4. Documentação e regressão

- [x] 4.1 Criar `docs/security.md` (níveis, detectores, ações, como adicionar cliente/projeto/regra, fail-closed, limitações do Presidio em PT) e atualizar `README.md` e `docs/architecture.md`; verificar seguindo `docs/security.md` para cadastrar um cliente confidencial e observar o efeito sem reiniciar
- [x] 4.2 Rodar `scripts/healthcheck.sh`, todos os testes unitários e os smoke tests (`gateway`, `model-access`, `quotas`, `rate-limit`, `security`, `network-isolation`); verificar que todos passam e registrar em `docs/testing.md`

## Workflow follow-up

- Arquivar com `/opsx:archive` após a validação; em seguida, planejar a Fase 6 (classificação semântica pela IA Local).
