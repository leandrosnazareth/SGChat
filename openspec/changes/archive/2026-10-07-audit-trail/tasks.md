# Tasks

## 1. Integração

- [x] 1.1 Credenciais do Langfuse no gateway (`docker-compose.yml`) e callbacks `langfuse` de sucesso e falha (`litellm/config.yaml`); verificar que um trace chega com usuário, sessão, entrada, saída, tokens, custo e latência
- [x] 1.2 `litellm/custom/audit_metadata.py` (D2, D3) registrado no início e no fim da cadeia; decisões em bloqueios (D4) e redação de `RESTRICTED` (D5); verificar decisões, tags, redação e o descarte de chaves do cliente nos traces

## 2. Testes

- [x] 2.1 `tests/unit/test_audit_metadata.py` (D7) e os demais unitários
- [x] 2.2 `scripts/audit-search.sh` (D6) e `tests/smoke/audit.sh` (D7); verificar que passam sem chamada ao Google
- [x] 2.3 Navegador: conversa no portal → trace no Langfuse com usuário, modelo pedido e utilizado, tokens, custo, classificação e motivo (Teste 8); filtros por tag e metadado na interface

## 3. Documentação e regressão

- [x] 3.1 `docs/audit.md` (o que é registrado, campos, busca na interface e no script, redação, limitações) e atualização de `README.md`, `docs/architecture.md`, `docs/security.md`
- [x] 3.2 Regressão completa (healthcheck, unitários, avaliação, todos os smoke tests); registrar em `docs/testing.md`; arquivar

## Workflow follow-up

- Fase 9: perfis `AUDITOR`/`MASTER_AUDITOR`, `VIEW_CONVERSATION`/`EXPORT_CONVERSATION`/`SEARCH_CONVERSATIONS`, trilha imutável do auditor.
