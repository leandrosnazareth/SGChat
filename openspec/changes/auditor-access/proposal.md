# Proposal

## Why

Desde a Fase 8, toda conversa fica no Langfuse com conteúdo, decisões e custos. Mas quem pode ler isso e quem leu ainda não é controlado:
- a interface do Langfuse não registra leituras;
- a conta de administrador do Langfuse e as chaves do projeto dão acesso total, sem trilha.

O `prompt.txt` (seções 19–21 e Fase 9) e o documento de software (§10.2, RNF-008) pedem:
- perfis `AUDITOR` e `MASTER_AUDITOR`, distintos de `ADMIN`;
- registro de `VIEW_CONVERSATION`, `EXPORT_CONVERSATION` e `SEARCH_CONVERSATIONS`;
- trilha **imutável** do acesso do auditor ("quem audita também é auditado");
- menor privilégio.

## What Changes

- **Portal de auditoria** (`audit-portal`, novo serviço Docker, FastAPI, http://localhost:3090). É o caminho dos auditores até o registro:
  - **Login** com a conta do portal de chat (validada pelo LibreChat); o perfil vem dos grupos `AUDITOR`/`MASTER_AUDITOR` de `model-access.yaml`. `ADMIN` e demais grupos não entram.
  - **`AUDITOR`**: busca por usuário, conversa, período, classificação, modelos, motivo, bloqueios e chamadas externas. Vê só metadados (quem, quando, modelos, decisão, tokens, custo), **sem conteúdo**.
  - **`MASTER_AUDITOR`**: também busca por termo, abre e exporta conversas (pergunta, resposta, decisões) **informando um motivo**, e consulta a trilha de acesso.
  - Permissões por perfil em `audit-portal/policy.yaml`, recarregadas sem reiniciar; inválida → ninguém entra.
- **Trilha de acesso imutável** (`AUDIT_ACCESS`) no PostgreSQL, banco `audit`:
  - cada busca, visualização, exportação, consulta à trilha, login e negação vira um evento com auditor, perfil, alvo (usuário e conversa), filtros, motivo, quantidade de resultados, IP e horário;
  - gravação só pela função `append_access_event`, que encadeia hashes SHA-256 (cada evento inclui o hash do anterior);
  - `UPDATE`, `DELETE` e `TRUNCATE` são barrados por trigger; o usuário do portal só tem `EXECUTE` e `SELECT`;
  - `verify_access_chain()` e `scripts/audit-access-verify.sh` detectam adulteração;
  - **fail-closed**: sem gravar o evento, o portal não mostra o resultado.
- `scripts/audit-search.sh` (operação) também passa a gravar `SEARCH_CONVERSATIONS`/`VIEW_CONVERSATION` na trilha (`channel=cli`).
- Usuários de teste: auditora (`AUDITOR`), auditor master (`MASTER_AUDITOR`) e administrador (`ADMIN`).
- Testes unitários e de fumaça, validação no navegador e `docs/audit.md` (Fase 9).

### Fora do escopo

- **Retenção por classificação** (`*_RETENTION_DAYS`) e **`STORE_FULL_CONTENT`** (seções 22–23): Fase 10.
- **Leituras pela interface do Langfuse**: continuam sem registro. A conta do Langfuse fica restrita à operação da plataforma (break-glass) e o risco é documentado; restringir ou despublicar a interface é decisão da Fase 10.
- SSO/OIDC para auditores; aprovação de acesso por um segundo auditor (dupla custódia).

### Impacto em segurança

- **Menor privilégio**: conteúdo só para `MASTER_AUDITOR`, sempre com motivo registrado. `AUDITOR` vê metadados. `ADMIN` não tem acesso ao portal.
- As chaves do Langfuse ficam só no servidor do portal (e no gateway); o navegador do auditor nunca as recebe.
- O conteúdo exibido é escapado (Jinja2 autoescape). As páginas não têm JavaScript, usam CSP restritiva e `Cache-Control: no-store`.
- A trilha resiste a alteração pelo próprio portal. O superusuário do banco ainda pode adulterar, mas a cadeia de hashes revela a alteração; a remoção dos últimos eventos é detectada pela cópia de cada hash no log do container.

## Capabilities

### New Capabilities

- `auditor-access`: perfis de auditoria, portal com busca e leitura controladas, e trilha imutável do acesso.

### Modified Capabilities

- `platform-deployment`: o portal de auditoria passa a ser uma interface publicada no host (requisito de portas publicadas).

## Impact

- **Novos**:
  - `audit-portal/` (Dockerfile, app, templates, `policy.yaml`, `sql/`);
  - serviços `audit-portal` e `audit-db-init`;
  - rede interna `audit-net`;
  - `scripts/audit-access-verify.sh`;
  - testes `tests/unit/test_audit_portal.py` e `tests/smoke/audit-portal.sh`.
- **Alterados**: `docker-compose.yml`, `.env.example` (`AUDIT_DB_PASSWORD`, `AUDIT_SESSION_SECRET`, `AUDIT_PORTAL_PORT`), `litellm/policies/model-access.yaml` (usuários de teste), `scripts/healthcheck.sh` (porta), `scripts/audit-search.sh`, `tests/smoke/network-isolation.sh`, documentação.
