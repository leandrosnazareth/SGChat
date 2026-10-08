# Design

## Context

- **Registro**: Langfuse 4.53.0 (Fase 8), consultado pela API pública v4 `/api/public/v2/observations` com as chaves do projeto.
  - A interface do Langfuse não registra leituras e o RBAC por projeto é recurso Enterprise.
  - Logo, a trilha de acesso precisa estar num caminho controlado por nós.
- **Identidade**: contas do LibreChat (login local, auto-registro desligado). `POST /api/auth/login` responde 200 com `user.email` quando as credenciais são válidas e 404 quando não são. Tem limite nativo de 7 tentativas por IP e janela.
- **Papéis**: `litellm/policies/model-access.yaml` já tem os grupos `AUDITOR` e `MASTER_AUDITOR` e mapeia e-mail → grupos. É a fonte única de grupos da plataforma.

## Goals / Non-Goals

**Goals:**
- auditores separados de administradores;
- menor privilégio (metadados × conteúdo);
- motivo obrigatório para conteúdo;
- todo acesso registrado em trilha imutável e verificável;
- fail-closed.

**Non-Goals:**
- retenção e `STORE_FULL_CONTENT` (Fase 10);
- registrar leituras na interface do Langfuse;
- SSO;
- dupla custódia.

## Decisions

### D1. Serviço `audit-portal`
- **Imagem**: FastAPI + Uvicorn + Jinja2, construída de `audit-portal/Dockerfile` sobre `python:3.13.13-slim`, com dependências fixadas: fastapi 0.142.4, uvicorn 0.54.0, jinja2 3.1.6, httpx 0.28.1, psycopg 3.3.6, python-multipart 0.0.32, pyyaml 6.0.3. Roda como usuário sem privilégio, com sistema de arquivos somente leitura.
- **Porta**: `${AUDIT_PORTAL_PORT:-3090}`.
- **Redes**:
  - `edge`: publicação da porta;
  - `frontend-net`: login no LibreChat;
  - `observability-net`: API do Langfuse;
  - **`audit-net`** (nova, interna): só `audit-portal` e `postgres`.
  - Não entra em `data-net`, então não alcança ClickHouse, Redis nem MinIO.
- **Montagens** (somente leitura): `litellm/policies` (grupos) e `audit-portal/policy.yaml`.
- **Health check**: `/health`, que inclui a conexão com o banco da trilha.

### D2. Login e sessão
- O login envia e-mail e senha ao LibreChat (`http://librechat:3080/api/auth/login`) com `X-Forwarded-For` = IP do auditor, para o limite de tentativas valer por auditor. Em seguida encerra a sessão aberta no LibreChat (`/api/auth/logout`).
  - 200 → grupos do e-mail em `model-access.yaml` → permissões em `policy.yaml`. Sem nenhuma permissão: evento `LOGIN` `DENIED`.
  - Credenciais inválidas: evento `LOGIN` `FAILED`.
- **Sessão**: cookie `audit_session` assinado (HMAC-SHA256 com `AUDIT_SESSION_SECRET`).
  - Conteúdo: e-mail, ID da sessão e expiração (`session_minutes`, padrão 30).
  - Atributos `HttpOnly` e `SameSite=Strict`.
  - O logout revoga o ID em memória.
  - **As permissões são recalculadas a cada requisição**: tirar o usuário do grupo vale imediatamente.
- **CSRF**: formulários com token derivado da sessão (HMAC); a API JSON exige `Content-Type: application/json`.

### D3. Permissões (`audit-portal/policy.yaml`)

```yaml
version: 1
roles:
  AUDITOR:        [SEARCH_CONVERSATIONS]
  MASTER_AUDITOR: [SEARCH_CONVERSATIONS, SEARCH_CONTENT, VIEW_CONVERSATION, EXPORT_CONVERSATION, VIEW_ACCESS_LOG]
reason_required: [VIEW_CONVERSATION, EXPORT_CONVERSATION]
reason_min_chars: 10
session_minutes: 30
max_results: 200
```

- `SEARCH_CONTENT` é a busca por termo nas mensagens: ela revela conteúdo, então só o master tem.
- A política é recarregada por mtime. Se for inválida, ninguém entra (fail-closed).

### D4. Busca, visualização e exportação

| Operação | Permissão | O que mostra | Evento |
|---|---|---|---|
| Busca: usuário, conversa, período, classificação, pedido/efetivo, motivo, bloqueadas, externas (+ termo, com `SEARCH_CONTENT`) | `SEARCH_CONVERSATIONS` | uma linha por chamada: horário, usuário, conversa, pedido → efetivo, classificação, motivo, `security_reasons`, tokens, custo, status. **Sem entrada nem saída** | `SEARCH_CONVERSATIONS` com filtros e quantidade |
| Conversa (`sessionId`), ou chamada avulsa (`traceId`) quando não há sessão | `VIEW_CONVERSATION` + motivo | perguntas e respostas em ordem cronológica, com as decisões | `VIEW_CONVERSATION` com conversa, usuários-alvo, motivo e quantidade |
| Exportação | `EXPORT_CONVERSATION` + motivo | JSON (`attachment`) com as chamadas, decisões e o `access_event_id` | `EXPORT_CONVERSATION` |
| Trilha | `VIEW_ACCESS_LOG` | eventos mais recentes e o resultado de `verify_access_chain()` | `VIEW_ACCESS_LOG` |

- Toda negação por falta de permissão gera evento com resultado `DENIED`.
- **Ordem**: grava o evento → só então busca e entrega. Se a gravação falhar, a resposta é 503 e nenhum dado é entregue.
- O evento de busca é gravado antes de buscar, com a quantidade de resultados `null`. Para não exigir atualização (a trilha não aceita `UPDATE`), a quantidade não é registrada nesse evento. Na visualização e na exportação, a quantidade vem de uma consulta de contagem feita antes de gravar o evento; o conteúdo só é lido depois de gravado.

### D5. Trilha imutável (PostgreSQL, banco `audit`)
- **Instalação**: o one-shot `audit-db-init` (imagem `postgres:17.11`, script idempotente `audit-portal/sql/init.sh` executado como superusuário na `audit-net`) cria:
  - o papel `audit_portal`;
  - o banco `audit`, de propriedade de `postgres`;
  - o schema `audit`, a tabela, os triggers e as funções.
  - Vale para volumes novos e existentes.
- **Tabela `audit.access_log`**:
  - `id`, `occurred_at` (`clock_timestamp()`), `event_type='AUDIT_ACCESS'`;
  - `action`, `outcome` (`ALLOWED`, `DENIED`, `FAILED`), `actor`, `actor_roles`, `channel` (`portal`, `cli`);
  - `target_users`, `conversation_id`, `trace_id`, `query` (jsonb), `reason`, `result_count`, `client_ip`;
  - `prev_hash`, `hash`.
- **`audit.append_access_event(...)`** (`SECURITY DEFINER`, `search_path` fixo):
  - `pg_advisory_xact_lock` → lê o último `hash` → `hash = sha256(prev_hash | id | occurred_at UTC | action | … | query::text)` → `INSERT`;
  - retorna `id` e `hash`.
- **Proteções**:
  - triggers `BEFORE UPDATE OR DELETE` e `BEFORE TRUNCATE` levantam exceção;
  - `audit_portal` tem apenas `EXECUTE` nas funções e `SELECT` na tabela; `PUBLIC` sem acesso.
- **Verificação**: `audit.verify_access_chain()` recalcula a cadeia e retorna total, primeiro `id` inválido e hash da cabeça. `scripts/audit-access-verify.sh` a executa.
- **Cópia fora do banco**: o portal escreve cada evento (id, ação, ator, hash) no log do container. A cópia ajuda a detectar remoção dos últimos eventos pelo superusuário.

### D6. CLI de operação auditada
`scripts/audit-search.sh` grava na trilha antes de consultar, via `docker compose exec postgres psql -U postgres -d audit` e a mesma função:
- `actor = <usuário do SO>@<host>`;
- `actor_roles = {OPERATOR}`, `channel = cli`;
- ação `SEARCH_CONVERSATIONS`, ou `VIEW_CONVERSATION` com `--show`;
- motivo opcional por `--reason-text`; filtros em `query`.

Se a gravação falhar, a consulta não é feita.

### D7. Usuários de teste
Criados com `scripts/create-user.sh … --save` e adicionados a `model-access.yaml`:
- `carla.auditora@corporate-ai.local` [AUDITOR];
- `diego.master@corporate-ai.local` [MASTER_AUDITOR];
- `edu.admin@corporate-ai.local` [ADMIN].

### D8. Testes
- **Unitários** (`tests/unit/test_audit_portal.py`, dentro do container do portal):
  - política (validação, recarga, fail-closed);
  - permissões por grupos;
  - sessão (assinatura, expiração, adulteração, revogação);
  - CSRF;
  - montagem de filtros (termo só com `SEARCH_CONTENT`);
  - formatação sem conteúdo para `AUDITOR`;
  - ordem "grava antes de entregar", com o banco simulado.
- **Smoke** (`tests/smoke/audit-portal.sh`): cria uma conversa pela IA Local e verifica:
  - sem sessão → 401;
  - senha errada → `FAILED`; `ADMIN` → `DENIED`;
  - `AUDITOR`: busca só com metadados, conversa → 403 `DENIED`, termo → 403;
  - `MASTER_AUDITOR`: sem motivo → 400 e nenhum conteúdo; com motivo → conteúdo; exportação → anexo; trilha consultada;
  - eventos com os campos certos;
  - `UPDATE`/`DELETE` pelo papel do portal → negado; pelo superusuário → trigger;
  - adulteração em transação revertida → verificação acusa;
  - `EXECUTE` revogado temporariamente → 503 sem conteúdo;
  - CLI grava `channel=cli`;
  - verificação da cadeia OK no fim.
  - Usa no máximo 4 logins (limite do LibreChat).
- **Navegador**: master busca, abre e exporta com motivo e consulta a trilha; auditor não vê conteúdo; admin é recusado.

## Risks / Trade-offs

- **Leituras pela interface do Langfuse e pelas chaves do projeto não passam pela trilha.** Mitigação: só o portal é ferramenta de auditoria, a conta do Langfuse é de operação (break-glass) e o CLI é auditado. Restringir a interface fica para a Fase 10.
- **O superusuário do banco pode adulterar** → detectável pela cadeia de hashes e pela cópia no log. Ancoragem externa (WORM) fica para produção.
- **Limite de login do LibreChat por IP** → `X-Forwarded-For`; os testes usam poucos logins.
- **Sessões revogadas em memória** (uma réplica) → um reinício encerra todas as sessões, o que é seguro.

## Migration Plan

Gerar os secrets novos (`scripts/init-env.sh`), `docker compose up -d --build` (cria `audit-net`, roda `audit-db-init`, sobe o portal) e criar os usuários de teste. Rollback: remover os serviços. O banco `audit` permanece (a trilha não deve ser apagada).
