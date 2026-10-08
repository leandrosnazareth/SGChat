# Design

## Context

Hooks do gateway (LiteLLM v1.104.0), em ordem: `model_access` (ACL) → `security_router` (classificação; não público vai para `local-ai`) → `quota_policy` (cota/budget; fallback para `local-ai`). Padrões já validados: redirecionar trocando `data["model"]` no `async_pre_call_hook`, políticas YAML recarregadas por mtime e decisões em `spend_logs_metadata`.

O LiteLLM tem roteamento automático nativo (`auto_router/complexity_router`, `router_strategy/complexity_router/`). Ele classifica por palavras-chave e por LLM e escolhe entre faixas **dentro do Router**, depois dos hooks de pré-chamada, sem conhecer o usuário final, a política de acesso ou as cotas. Usá-lo como `auto` permitiria, por exemplo, que um usuário de FINANCE chegasse ao `gemini`.

## Goals / Non-Goals

**Goals:** `auto` respeitando segurança, ACL e cota, com escolha explicável, configurável e sem latência relevante.

**Non-Goals:** classificar complexidade com LLM; balanceamento entre réplicas; roteamento adaptativo por feedback.

## Decisions

### D1. `auto` no catálogo e na ACL
- `litellm/config.yaml`: `model_name: auto` apontando para o **mesmo backend da IA Local**. Se o hook não atuar por algum motivo, a requisição vai para a IA Local, o caminho seguro.
- `model-access.yaml`: `auto` em todos os grupos. A listagem filtrada passa a mostrá-lo e a ACL o permite.
- O Security Router trata `auto` como externo (não está em `local_models`), então tudo é classificado, inclusive pelo classificador semântico.

### D2. `ModelRouter` (hook `custom/model_router.py`)
Registrado entre `security_router` e `quota_policy`. Só atua se `data["model"] == auto_model`; se a segurança já trocou para `local-ai`, nada faz.
1. **Candidatos**: modelos de `routing.yaml > models`.
2. **Permissão**: remove os não permitidos ao usuário (política de acesso carregada pelo caminho do módulo). Motivo `permission`.
3. **Cota e budget**: para cada candidato restante, `QuotaPolicy.evaluate` da **instância registrada** no LiteLLM (encontrada em `litellm.callbacks`, para compartilhar a janela de memória). Motivos `quota` e `budget`. Se a instância não for encontrada, exclui os modelos fora de `unlimited_models` (fail-closed: `quota_unknown`).
4. **Complexidade**: pontuação por sinais sobre a **última mensagem do usuário** e a conversa:

   | Sinal | Pontos |
   |---|---|
   | tamanho ≥ 800 caracteres | +2 |
   | tamanho ≥ 3000 caracteres | +2 |
   | bloco de código | +2 |
   | ≥ 3 interrogações | +1 |
   | conversa com ≥ 6 mensagens | +1 |
   | termos de análise/planejamento (palavra inteira, sem acentos) | +2 (uma vez) |

   Faixas: `simple` (≤ 1), `medium` (≤ 3), `complex` (> 3) → `min_quality` 1, 2, 4.
5. **Disponibilidade**: `async_log_failure_event` registra falhas por modelo; ≥ `failure_threshold` (2) em `window_seconds` (120) → indisponível por `cooldown_seconds` (60). Motivo `unavailable`.
6. **Escolha**: entre os candidatos com `quality ≥ min_quality`, o de menor `cost` (empate → maior `quality`) — regra `cheapest_meeting_quality`. Se nenhum atinge, o de maior qualidade — `best_available`. Sem candidatos → `fallback_model` se a ACL permitir (`no_candidate_fallback`); senão `HTTPException(403, MODEL_ACCESS_DENIED)`.
7. **Metadados**:
   - `requested_model=auto`, `effective_model`, `routing_reason=AUTO`;
   - `auto_decision = {tier, score, signals[], min_quality, rule, chosen, excluded{modelo: motivo}}`.

   Nunca inclui o texto da requisição.

### D3. Perfil inicial (`routing.yaml`)

```yaml
version: 1
auto_model: auto
fallback_model: local-ai
models:
  local-ai: {quality: 2, cost: 0}
  gemini:   {quality: 4, cost: 1}
complexity: {tiers: …, signals: …}     # D2.4
availability: {failure_threshold: 2, window_seconds: 120, cooldown_seconds: 60}
```

Efeito com o catálogo atual:
- `simple` e `medium` → `local-ai` (custo zero, qualidade suficiente);
- `complex` → `gemini`, se permitido, com cota e disponível; senão, `local-ai` (`best_available`).

### D4. Interação com a cota
A cota é avaliada duas vezes: no `ModelRouter`, para não escolher um modelo esgotado, e no `quota_policy`, para o modelo escolhido. A segunda avaliação continua sendo a barreira: se a cota acabar entre as duas, o fallback da Fase 4 vale. O `quota_policy` grava `routing_reason` com `setdefault`, então a decisão `AUTO` é preservada, salvo fallback por cota, que sobrescreve com o motivo da cota (comportamento desejado).

### D5. Testes
- **Unitários** (`tests/unit/test_model_router.py`):
  - sinais e faixas;
  - candidatos por ACL, cota (quota simulada), indisponibilidade;
  - escolha (mais barato, melhor disponível, fallback, 403);
  - não atua fora de `auto` e respeita redirecionamento da segurança;
  - metadados sem texto; recarga da política.
- **Smoke** (`tests/smoke/auto.sh`, gateway real):
  - listagem com `auto`;
  - simples → `local-ai`;
  - complexo e público (Ana) → `gemini` (1 chamada real);
  - complexo com cota zero (regra temporária) → `local-ai` com `excluded.gemini=quota`;
  - Bruno (FINANCE) complexo → `local-ai` com `excluded.gemini=permission`;
  - confidencial → `SECURITY_POLICY`.
- **Navegador**: Ana seleciona `auto`; pergunta simples → IA Local; pedido complexo → Gemini; registros.

## Risks / Trade-offs

- **Heurística de complexidade simples** (pode subestimar pedidos curtos e difíceis) → configurável, registrada para calibrar; o usuário pode escolher o modelo explicitamente.
- **Avaliar a cota no router acrescenta uma consulta** → a cota já tem cache de 3 s no banco e janela em memória.
- **Circuit breaker em memória (uma réplica)** → mesma premissa da janela de cotas.
- **O campo `model` da resposta mostra `auto`** → o modelo efetivo fica no registro de uso (mesma limitação dos fallbacks).

## Migration Plan

Adicionar `auto` ao config e à política de acesso, criar `routing.yaml` e o hook, reiniciar o `litellm` e recarregar o portal. Rollback: remover `auto` dos grupos (some do seletor) e o callback.
