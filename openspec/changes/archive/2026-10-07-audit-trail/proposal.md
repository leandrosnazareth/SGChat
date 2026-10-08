# Proposal

## Why

O Langfuse self-hosted está no ar desde a Fase 1, mas nada é enviado a ele. As decisões do gateway (classificação, modelo pedido e efetivo, motivo do roteamento, cota) estão só em `LiteLLM_SpendLogs`, sem o conteúdo, e não dá para reconstruir uma conversa. O `prompt.txt` (seção 18 e Fase 8) pede que **toda chamada** gere trace no Langfuse com usuário, conversa, entrada e saída, tokens, custo, latência, classificação, roteamento e erros, e que seja possível buscar por `user`, `classification`, `requestedModel`, `effectiveModel` e `routingReason`.

## What Changes

- **Callback nativo do LiteLLM** `langfuse` (sucesso e falha) para o Langfuse interno (`http://langfuse-web:3000`, rede `observability-net`), com as chaves do projeto criadas na inicialização do Langfuse. Cada chamada gera um trace com usuário (e-mail), sessão (ID da conversa do LibreChat), entrada e saída, modelo, tokens, custo, latência e nível `ERROR` em falhas e bloqueios.
- **Hook `audit_metadata`** (`litellm/custom/audit_metadata.py`), em dois pontos da cadeia:
  - **início**: descarta do pedido do cliente qualquer chave que altere o registro (mascaramento, `trace_*`, `tags`, credenciais `langfuse_*`, cabeçalhos de redação) e liga os metadados do trace às decisões dos hooks seguintes;
  - **fim**: tags pesquisáveis (`classification:`, `requested:`, `effective:`, `routing:`).
- **Requisições bloqueadas** (acesso negado, segurança, cota em `BLOCK`, AUTO sem candidato) também aparecem, com `blocked=true` e o motivo.
- **Segredos não vão para o Langfuse**: em bloqueio `RESTRICTED`, o Security Router substitui o conteúdo por `[REDACTED: RESTRICTED — <regras>]` antes do 403.
- `scripts/audit-search.sh`: busca por usuário, conversa, período, classificação, modelo pedido/efetivo, motivo e bloqueios, pela API pública do Langfuse v4.
- Testes de fumaça (`tests/smoke/audit.sh`), unitários do hook, validação na interface do Langfuse e `docs/audit.md`.

### Fora do escopo

- Perfis `AUDITOR`/`MASTER_AUDITOR` e trilha de acesso do auditor (Fase 9).
- Política de retenção configurável (Fase 9/10).
- Garantia de entrega com o Langfuse fora do ar (Fase 11): hoje o gateway continua atendendo e o SDK tenta reenviar; os metadados da decisão permanecem em `LiteLLM_SpendLogs`.
- Traces das consultas do classificador semântico à IA Local (chamadas diretas ao Ollama, fora do LiteLLM).

### Impacto em segurança

- O Langfuse passa a guardar o **conteúdo** das conversas (necessário para a auditoria). Ele roda só nas redes internas; a interface é publicada no host para a equipe de auditoria.
- Conteúdo `RESTRICTED` (segredos) nunca é persistido: é substituído antes do registro.
- O cliente do gateway não consegue desligar, mascarar ou redirecionar o registro da própria requisição.

## Capabilities

### New Capabilities

- `audit-trail`: trace no Langfuse para toda chamada, com decisões pesquisáveis, bloqueios registrados e segredos removidos.

### Modified Capabilities

Nenhuma.

## Impact

- **Novos**: `litellm/custom/audit_metadata.py`, `scripts/audit-search.sh`, `tests/unit/test_audit_metadata.py`, `tests/smoke/audit.sh`, `docs/audit.md`.
- **Alterados**:
  - `litellm/config.yaml` (callbacks);
  - `docker-compose.yml` (credenciais do Langfuse no gateway);
  - `litellm/custom/security_router.py` (redação em `RESTRICTED`; `blocked`);
  - `model_access.py`, `model_router.py`, `quota_policy.py` (decisão registrada em bloqueios);
  - `README.md`, `docs/architecture.md`, `docs/security.md`, `docs/testing.md`.
