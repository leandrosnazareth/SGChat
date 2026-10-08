# Proposal

## Why

Desde a Fase 3 o gateway bloqueia modelos não permitidos (HTTP 403 `MODEL_ACCESS_DENIED`), mas o seletor do LibreChat continua listando todos para todos os usuários. Um usuário de FINANCE vê `gemini`, escolhe e só descobre que não pode no erro. O RF-002 do documento de software exige "listar apenas modelos visíveis conforme usuário/grupo". Esta mudança completa a Fase 3 (`prompt.txt`, seção 28: "filtros de modelos"), adiada de propósito na mudança `model-access-control`.

## What Changes

- A listagem de modelos do gateway (`/v1/models` e rotas relacionadas) passa a devolver **apenas os modelos que a política permite** ao usuário que pede. Modelos ocultos respondem 404 também na consulta individual.
- **Identidade unificada**: o portal passa a enviar o **e-mail** do usuário como identificador de usuário final do LiteLLM (`x-litellm-end-user-id`). O bloqueio (Fase 3) e a listagem usam essa mesma identidade nativa. O cabeçalho próprio `x-corporate-user-email` é removido.
- O LibreChat, que já busca a lista de modelos por usuário e sem cache compartilhado quando o endpoint tem cabeçalhos com dados do usuário, passa a exibir só os modelos permitidos.
- Lista de reserva do portal (`models.default`, usada quando a busca falha ou volta vazia) reduzida a `local-ai`, para que uma falha do gateway não volte a exibir modelos externos.
- Testes de fumaça da listagem por usuário e validação no navegador com Ana (DEVELOPER) e Bruno (FINANCE).

### Fora do escopo

- Cotas, budgets e rate limits (Fase 4).
- Ocultar modelos de conversas antigas que já usaram um modelo agora negado: elas continuam abertas, e uma nova mensagem com esse modelo continua bloqueada pelo gateway.
- Mudar a política de grupos.

### Impacto em segurança

A barreira real continua no gateway e não muda (Fase 3). A filtragem é de visibilidade: reduz tentativa e erro e exposição desnecessária. A identidade continua vindo de um cabeçalho que só o portal envia com a chave dele; a limitação já registrada na Fase 3 permanece (endurecimento na Fase 10). Listagens sem identidade, com chave que não seja a mestra, devolvem lista vazia (fail-closed).

**Efeito colateral observável:** o campo `end_user` dos registros de uso (`LiteLLM_SpendLogs`) passa a conter o e-mail do usuário em vez do ID interno do LibreChat. Registros antigos permanecem com o ID. É mais legível para auditoria e alinha com as cotas por usuário do LiteLLM (Fase 4), que usam esse mesmo identificador.

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

- `model-access`: a identidade passa a ser o identificador de usuário final do gateway (o e-mail), e a listagem de modelos passa a ser filtrada pela política (novo requisito).
- `chat-portal`: o seletor mostra apenas os modelos permitidos ao usuário; a propagação de identidade envia o e-mail como identificador de usuário final.

## Impact

- **Alterados**: `litellm/custom/model_access.py` (identidade por `end_user_id`, hook `async_filter_listed_models`), `librechat/librechat.yaml` (cabeçalhos e `models.default`), `tests/unit/test_model_access.py`, `tests/smoke/model-access.sh`, `tests/smoke/gateway.sh`, `docs/models.md`, `docs/architecture.md`, `docs/testing.md`.
- **Dados**: novos registros de uso com `end_user` = e-mail.
- **Dependências**: nenhuma nova; usa `CustomLogger.async_filter_listed_models` do LiteLLM v1.104.0.
