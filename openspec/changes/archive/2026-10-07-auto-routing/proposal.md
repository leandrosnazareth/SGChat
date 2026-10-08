# Proposal

## Why

Hoje o usuário precisa escolher entre `local-ai` e `gemini` a cada conversa, sem saber qual é o mais adequado, mais barato ou se ainda tem cota. O `prompt.txt` (seção 12 e Fase 7) pede o modelo **AUTO**: o sistema escolhe o modelo considerando, nesta ordem, segurança, permissão, cota, budget, complexidade, disponibilidade, custo e qualidade necessária, e registra `requestedModel`, `effectiveModel` e `routingReason`.

## What Changes

- Novo modelo lógico **`auto`** no catálogo do gateway, visível no seletor do portal para os grupos que o tiverem na política de acesso (todos, inicialmente).
- **`ModelRouter`** (hook próprio `litellm/custom/model_router.py`), executado depois da segurança e antes da cota. Para requisições a `auto`:
  1. **Segurança**: o Security Router já roda antes. Conteúdo não público vai para a IA Local (`routing_reason=SECURITY_POLICY`) e o `ModelRouter` não atua.
  2. **Permissão**: candidatos = modelos do perfil de roteamento que a política de acesso permite ao usuário.
  3. **Cota e budget**: exclui candidatos com cota de tokens ou budget esgotado, consultando o mesmo `quota_policy` do gateway.
  4. **Complexidade**: pontuação determinística da requisição (tamanho, código, perguntas múltiplas, termos de análise e planejamento, tamanho da conversa) → faixa `simple`/`medium`/`complex` → qualidade mínima.
  5. **Disponibilidade**: exclui modelos com falhas recentes (circuit breaker em memória).
  6. **Custo e qualidade**: escolhe o candidato **mais barato** que atinge a qualidade mínima; se nenhum atinge, o de **maior qualidade** disponível.
  7. Sem candidatos: IA Local, se permitida; senão, 403.
- Perfis, faixas, sinais e limites em `litellm/policies/routing.yaml`, alteráveis sem reiniciar.
- Registro: `requested_model=auto`, `effective_model`, `routing_reason=AUTO` e `auto_decision` (faixa, pontuação, sinais, regra de escolha, candidatos excluídos e por quê).
- Testes unitários e de fumaça, validação no navegador e `docs/routing.md`.

### Fora do escopo

- O roteador nativo por complexidade do LiteLLM (`auto_router/complexity_router`): ele decide depois dos hooks, sem conhecer o usuário, a política de acesso e as cotas, e poderia mandar um usuário sem permissão para o modelo externo.
- Classificar a complexidade com um modelo de linguagem (latência e custo extras); fica como evolução.
- Novos provedores (OpenAI, Claude); o roteador já aceita mais modelos no perfil quando forem adicionados.
- Aprendizado a partir de feedback e roteamento por qualidade observada.

### Impacto em segurança

Nenhum enfraquecimento: `auto` é tratado como modelo **externo** pelo Security Router, então todo conteúdo passa pelas regras, pelo Presidio e pelo classificador semântico **antes** da escolha. O `ModelRouter` só escolhe entre modelos que a política de acesso permite, e a cota é verificada novamente depois da escolha. Sem decisão possível, a escolha cai na IA Local.

## Capabilities

### New Capabilities

- `auto-routing`: modelo `auto` com escolha automática pela ordem segurança → permissão → cota/budget → complexidade → disponibilidade → custo/qualidade, configurável e registrada.

### Modified Capabilities

- `chat-portal`: o seletor passa a oferecer `auto` aos usuários cuja política o permita (muda o cenário de visibilidade por grupo).

## Impact

- **Novos**: `litellm/custom/model_router.py`, `litellm/policies/routing.yaml`, `tests/unit/test_model_router.py`, `tests/smoke/auto.sh`, `docs/routing.md`.
- **Alterados**: `litellm/config.yaml` (modelo `auto` e callback), `litellm/policies/model-access.yaml` (`auto` nos grupos), `docker-compose.yml` (`ROUTING_POLICY_PATH`), `README.md`, `docs/architecture.md`, `docs/models.md`, `docs/testing.md`.
