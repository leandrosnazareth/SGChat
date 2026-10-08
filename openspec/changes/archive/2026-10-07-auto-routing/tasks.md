# Tasks

## 1. Catálogo, política e configuração

- [x] 1.1 Adicionar o modelo `auto` em `litellm/config.yaml` (backend da IA Local) e em todos os grupos de `model-access.yaml`; criar `litellm/policies/routing.yaml` (D3) e `ROUTING_POLICY_PATH` no compose; verificar YAML válido e `docker compose config`

## 2. ModelRouter

- [x] 2.1 Implementar `litellm/custom/model_router.py` (D2: política com validação e recarga, candidatos, ACL, cota via instância registrada, complexidade, disponibilidade, escolha, fallback, metadados) e registrá-lo entre `security_router` e `quota_policy`; verificar que o módulo importa e o gateway sobe `healthy` com a política carregada
- [x] 2.2 Criar `tests/unit/test_model_router.py` (D5) e verificar que passa; rodar de novo os demais unitários

## 3. Validação

- [x] 3.1 Criar `tests/smoke/auto.sh` (D5) e verificar que passa com no máximo 1 chamada real ao Gemini e as políticas restauradas
- [x] 3.2 Validar no navegador: Ana vê `auto` no seletor; pergunta simples → IA Local; pedido complexo e público → Gemini; Bruno vê `auto` e `local-ai`. Registrar em `docs/testing.md`

## 4. Documentação e regressão

- [x] 4.1 Criar `docs/routing.md` (ordem da decisão, sinais, faixas, perfis, disponibilidade, auditoria, por que não o roteador nativo) e atualizar `README.md`, `docs/architecture.md` e `docs/models.md`; verificar seguindo o guia para alterar uma faixa e observar o efeito sem reiniciar
- [x] 4.2 Rodar `scripts/healthcheck.sh`, todos os unitários, a avaliação do classificador e todos os smoke tests; verificar que passam e registrar em `docs/testing.md`

## Workflow follow-up

- Arquivar com `/opsx:archive` após a validação; em seguida, planejar a Fase 8 (Langfuse e auditoria).
