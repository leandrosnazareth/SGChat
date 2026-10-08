# Tasks

## 1. Trilha e infraestrutura

- [x] 1.1 `audit-portal/sql/init.sh` (D5) e serviço `audit-db-init`, rede `audit-net`, secrets no `.env.example`; verificar idempotência, `append_access_event`, triggers, privilégios e `verify_access_chain`
- [x] 1.2 `scripts/audit-access-verify.sh`

## 2. Portal

- [x] 2.1 `audit-portal/` (D1–D4): app, templates, `policy.yaml`, Dockerfile; serviço no compose; verificar `healthy`, login e páginas
- [x] 2.2 Usuários de teste (D7) e `model-access.yaml`
- [x] 2.3 `scripts/audit-search.sh` auditado (D6)

## 3. Testes

- [x] 3.1 `tests/unit/test_audit_portal.py` e `tests/smoke/audit-portal.sh` (D8); `healthcheck.sh` e `network-isolation.sh` atualizados
- [x] 3.2 Navegador (D8)

## 4. Documentação e regressão

- [ ] 4.1 `docs/audit.md` (perfis, portal, trilha, verificação, riscos), `README.md`, `docs/architecture.md`
- [ ] 4.2 Regressão completa; registrar em `docs/testing.md`; arquivar

## Workflow follow-up

- Fase 10: retenção por classificação, `STORE_FULL_CONTENT`, restrição da interface do Langfuse e demais itens de hardening.
