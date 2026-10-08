# Design

## Context

O gateway já tem dois hooks próprios (`model_access`, `quota_policy`) que mostram os padrões que funcionam no LiteLLM v1.104.0:
- carga por caminho de arquivo, sem `from __future__ import annotations`;
- política YAML recarregada por mtime;
- redirecionamento trocando `data["model"]` no `async_pre_call_hook`;
- decisão gravada em `metadata.spend_logs_metadata`.

Testes no Presidio da stack (`mcr.microsoft.com/presidio-analyzer:2.2.362`, 07/10/2026):
- **Idioma**: só `en` (`pt` e `es` → HTTP 500).
- **Detectores por padrão, confiáveis em PT**: `EMAIL_ADDRESS`, `CREDIT_CARD`, `IBAN_CODE`.
- **Reconhecimento de entidades nomeadas, ruidoso em PT**:
  - "Explique Virtual Threads em Java" → `PERSON` 0,85;
  - "(11) 98765-4321" → `DATE_TIME`;
  - "São Paulo e" → `LOCATION`.
- CPF não é reconhecido.

## Goals / Non-Goals

**Goals:** classificar todo o contexto com regras configuráveis mais Presidio; impedir saída de `CONFIDENTIAL`/`INTERNAL`/`RESTRICTED`; bloquear `RESTRICTED`; fail-closed; registrar sem vazar conteúdo; latência baixa.

**Non-Goals:** classificador semântico (Fase 6); anonimização parcial; OCR e análise de anexos binários.

## Decisions

### D1. Ordem dos hooks: ACL → segurança → cota
`callbacks: [model_access, security_router, quota_policy]`.
- A permissão não depende do conteúdo. Avaliá-la primeiro evita analisar (e enviar ao Presidio) mensagens de quem não pode usar o modelo.
- A segurança vem **antes de qualquer chamada externa e antes da cota**: se ela redireciona para `local-ai`, a cota não se aplica (modelo ilimitado).

O documento de software lista "Segurança" antes de "Autorização". Na prática, as duas precisam passar, e nenhuma chamada externa acontece antes da segurança.

### D2. Política `litellm/policies/security.yaml`

```yaml
version: 1
local_models: [local-ai]          # modelos internos (o resto é externo)
local_model: local-ai             # destino quando o conteúdo não pode sair
actions:                          # modelo EXTERNO pedido: ALLOW | LOCAL | BLOCK
  PUBLIC: ALLOW
  INTERNAL: LOCAL
  CONFIDENTIAL: LOCAL
  RESTRICTED: BLOCK
restricted_on_local: BLOCK        # RESTRICTED pedido à IA Local: BLOCK | ALLOW
presidio: {url, language: en, score_threshold: 0.6, timeout_seconds: 10,
           entities: {EMAIL_ADDRESS: CONFIDENTIAL, CREDIT_CARD: RESTRICTED, IBAN_CODE: CONFIDENTIAL, CRYPTO: RESTRICTED}}
regex_rules: [{name, classification, pattern, flags?, validator?: cpf|cnpj}]
keywords: [{classification, terms: [...]}]          # palavra inteira, sem maiúsculas/acentos
classified_entities: [{name, kind: cliente|projeto, classification}]
internal_domains: [corporate-ai.local, empresa.local, intranet]
```

- `actions.RESTRICTED` aceita `LOCAL` ("quando permitido pela política").
- Validação no carregamento: níveis válidos, regex compiláveis, validadores conhecidos, `local_model` em `local_models`.
- Política inválida → fail-closed (D6).

### D3. Detectores (`SensitiveDataDetector`)
- **Texto normalizado** para palavras-chave e entidades: minúsculas, sem acentos (`unicodedata`), casando palavra inteira (`(?<!\w)…(?!\w)`).
- **Regex** sobre o texto original. `validator: cpf|cnpj` confere o dígito verificador e descarta sequências repetidas (`111.111.111-11`).
- **Regras iniciais**:

  | Classificação | Regras |
  |---|---|
  | `RESTRICTED` | `sk-…` (≥8), `AKIA…`, `AIza…`, `gh[pousr]_…`, `-----BEGIN … PRIVATE KEY-----`, JWT (`eyJ…​.eyJ…`), `Bearer <token>`, credencial declarada (`(senha\|password\|api key\|token\|secret\|segredo\|credencial)\s*(é\|:\|=)\s*\S+`) |
  | `CONFIDENTIAL` | CPF, CNPJ, telefone BR, IP privado (10/8, 172.16/12, 192.168/16), URL de domínio interno, número de contrato, valor em R$, bloco de código cercado (```` ``` ````) com ≥ 3 linhas |

- **Palavras-chave iniciais**:
  - `RESTRICTED`: restrito, restrita, estritamente confidencial;
  - `CONFIDENTIAL`: confidencial, confidenciais, sigiloso, sigilosa;
  - `INTERNAL`: uso interno.
- **Entidades classificadas** (exemplos editáveis): cliente `XPTO`, projeto `Atlas`.
- **Presidio** (`POST /analyze`, `language=en`): só entidades mapeadas em `presidio.entities` e com `score ≥ score_threshold`. O reconhecimento de nomes, lugares e datas fica fora por padrão (ruído em PT, ver Context).
- Cada evidência gera um achado `(classification, reason)`, onde `reason` = `regex:<nome>`, `keyword:<termo>`, `entity:<kind>:<nome>`, `presidio:<TIPO>` ou `detector_unavailable:presidio`. O trecho encontrado nunca é guardado.

### D4. Contexto analisado
Textos extraídos de:
- `messages[*].content` (texto ou partes `{"type":"text"}`) e `messages[*].tool_calls[*].function.arguments`;
- `input` (texto ou lista) e `prompt`.

Cada texto é analisado separadamente, com **cache LRU por hash SHA-256** (512 entradas). Como o portal reenvia o histórico a cada turno, só mensagens novas vão ao Presidio. A classificação final é a máxima entre todos os achados.

### D5. Decisão (`PolicyEngine`)
- Modelo externo pedido → `actions[classification]`. `LOCAL` troca `data["model"]` para `local_model`, se a ACL o permitir ao usuário; senão `BLOCK`.
- Modelo interno pedido → segue, exceto `RESTRICTED` com `restricted_on_local: BLOCK`.
- `BLOCK` → `HTTPException(403, {"error": "SECURITY_POLICY_BLOCKED", "classification": …})`.
- Chave mestra: analisada e registrada como as demais (a segurança não tem bypass administrativo).
- Metadados (`spend_logs_metadata`):
  - `classification`, `confidence` (1,0 nas regras determinísticas);
  - `external_allowed` (`actions[c] == ALLOW`);
  - `security_reasons` (até 20, sem duplicatas), `requested_model`, `effective_model`;
  - `routing_reason` = `SECURITY_POLICY` quando a segurança redirecionou.
- `quota_policy` passa a usar `setdefault` em `requested_model` e `routing_reason`, para não sobrescrever a decisão da segurança.

### D6. Fail-closed
- Presidio com erro ou timeout → achado `CONFIDENTIAL detector_unavailable:presidio`. As regras locais continuam valendo, então `RESTRICTED` segue sendo detectado e bloqueado.
- Política inválida ou exceção inesperada → classificação `CONFIDENTIAL` com motivo `security_check_unavailable`; com modelo externo, redireciona à IA Local.
- IA Local fora do ar: o LiteLLM já não tem fallback configurado (`num_retries: 0`, sem `fallbacks`), então a chamada redirecionada falha com erro e não vai à nuvem (teste 10 do `prompt.txt`).

### D7. Testes
- **Unitários** (`tests/unit/test_security_router.py`, Presidio simulado):
  - CPF/CNPJ válidos e inválidos, cada regex, palavras-chave com acentos e maiúsculas, entidades;
  - extração de contexto (histórico, partes, ferramentas);
  - consolidação, decisões, ACL sem `local-ai` → bloqueio, `restricted_on_local`;
  - fail-closed (Presidio e política), cache, metadados sem texto sensível.
- **Smoke** (`tests/smoke/security.sh`, gateway real):
  - testes 1, 2, 3, 5 do `prompt.txt`;
  - histórico confidencial, CPF válido × inválido;
  - Presidio parado → IA Local; política inválida → IA Local;
  - teste 10 (`local-ai` parado → erro, nenhuma chamada ao `gemini`, verificado nos registros);
  - só 1 chamada real ao Gemini (teste 1).
- **Navegador**: Ana escolhe `gemini` para "contrato confidencial do cliente XPTO" → resposta da IA Local; mensagem com senha → erro de bloqueio.

## Risks / Trade-offs

- **Falsos positivos** (ex.: "o que é informação confidencial?", qualquer valor em R$, código colado) → vão para a IA Local: seguro, mas pior qualidade. Mitigação: regras editáveis e motivos registrados para calibrar.
- **Falsos negativos** (dado sensível sem padrão reconhecível) → a Fase 6 (classificador semântico) reduz esse risco.
- **Latência do Presidio** (~50–300 ms por mensagem nova) → cache por mensagem; timeout de 10 s com fail-closed.
- **Texto grande** (documentos colados) → Presidio pode demorar; o timeout leva à IA Local (conservador).
- **Usuário não sabe que a resposta veio da IA Local** → registrado; aviso visual é mudança futura (mesma limitação das cotas).

## Migration Plan

Adicionar a política e o hook, ajustar `quota_policy`, atualizar callbacks e reiniciar o `litellm`. Rollback: remover `custom.security_router.handler` dos callbacks e reiniciar.
