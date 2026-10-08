# Tasks

## 1. Gateway

- [x] 1.1 Alterar `litellm/custom/model_access.py` para obter a identidade de `user_api_key_dict.end_user_id` (normalizada) no bloqueio e implementar `async_filter_listed_models` conforme D1–D2; atualizar `tests/unit/test_model_access.py` (filtro parcial, sem identidade, política indisponível, admin, ordem preservada; bloqueio via `end_user_id`) e verificar que todos os testes unitários passam
- [x] 1.2 Recriar o `litellm` e verificar com `curl` na chave do portal: `x-litellm-end-user-id` de Bruno → `/v1/models` = `[local-ai]` e `/v1/models/gemini` com a mesma resposta de um modelo inexistente (401 da rota restrita a admin no LiteLLM); Ana → `[gemini, local-ai]`; sem identidade → `[]`; chave mestra → ambos

## 2. Portal

- [x] 2.1 Atualizar `librechat/librechat.yaml` (`x-litellm-end-user-id: '{{LIBRECHAT_USER_EMAIL}}'`, remover `x-corporate-user-email`, `models.default: ['local-ai']`) e recriar o `librechat`; verificar que ele fica `healthy` e carrega a config sem erros

## 3. Testes e validação

- [x] 3.1 Atualizar `tests/smoke/model-access.sh` (identidade via `x-litellm-end-user-id` e casos de listagem do D4) e `tests/smoke/gateway.sh` (identidade e-mail; checagem do registro de uso por e-mail); verificar que ambos passam integralmente
- [x] 3.2 Validar no navegador: seletor do Bruno mostra só `local-ai`; seletor da Ana mostra `local-ai` e `gemini`; Bruno conversa com `local-ai` normalmente; verificar em `LiteLLM_SpendLogs` que o `end_user` das mensagens do portal é o e-mail. Registrar evidências em `docs/testing.md`
- [x] 3.3 Verificar a lista de reserva: com o `litellm` parado, o seletor (após recarregar) mostra apenas `local-ai`; religar e confirmar `scripts/healthcheck.sh` OK. Registrar em `docs/testing.md`

## 4. Documentação

- [x] 4.1 Atualizar `docs/models.md` (seletor filtrado, identidade única, `end_user` = e-mail, lista de reserva) e `docs/architecture.md` (cabeçalhos de identidade); verificar que nenhum arquivo do projeto fora do arquivo do OpenSpec ainda cita `x-corporate-user-email` (`grep -rn x-corporate-user-email --exclude-dir=archive`)
- [x] 4.2 Rodar `scripts/healthcheck.sh`, testes unitários, `tests/smoke/model-access.sh`, `tests/smoke/gateway.sh` e `tests/smoke/network-isolation.sh`; verificar que todos passam e registrar em `docs/testing.md`

## Workflow follow-up

- Revisar os artefatos e aplicar com `/opsx:apply` (ou pedindo para aplicar a mudança).
- Arquivar com `/opsx:archive` após a validação; em seguida, planejar a Fase 4 (cotas e budgets por `end_user_id`).
