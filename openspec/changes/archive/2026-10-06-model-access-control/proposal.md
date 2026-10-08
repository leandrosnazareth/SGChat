# Proposal

## Why

Hoje qualquer usuário autenticado no portal pode usar qualquer modelo do gateway, inclusive o externo `gemini`. O documento de software (§8) exige que cada usuário, grupo ou área tenha modelos liberados e que a autorização real aconteça no gateway, não na interface. Esta mudança implementa a **Fase 3** do roadmap (`prompt.txt`, seção 28: usuários, grupos, modelos permitidos e autorização no backend) e cria o primeiro hook próprio no LiteLLM, base técnica do Security Router (Fase 5).

## What Changes

- Política de acesso a modelos em arquivo YAML versionado (`litellm/policies/model-access.yaml`): grupos → modelos permitidos, usuários (e-mail) → grupos e um grupo padrão para usuários não listados. Grupos iniciais conforme o documento de software: `USER`, `DEVELOPER`, `FINANCE`, `MANAGER`, `ADMIN`, `AUDITOR`, `MASTER_AUDITOR`.
- Hook pré-chamada próprio no LiteLLM (`litellm/custom/`) que, antes de qualquer chamada a modelo, identifica o usuário, resolve seus grupos e nega com **HTTP 403 `MODEL_ACCESS_DENIED`** quando o modelo não é permitido. Usuário sem permissão não consegue usar o modelo nem por chamada direta ao gateway.
- O portal passa a enviar o e-mail do usuário autenticado ao gateway, que o usa como identidade para a política.
- Comportamento fail-closed: política ausente ou inválida, ou requisição do portal sem identidade, resultam em negação.
- A política é recarregada automaticamente quando o arquivo muda, sem reiniciar o gateway.
- Testes de fumaça da ACL e documentação de como administrar grupos (`docs/models.md`).

### Fora do escopo

- **Esconder do seletor do LibreChat** os modelos não permitidos (RF-002). Nesta fase o seletor continua listando todos; o bloqueio acontece no gateway. A visibilidade fica para uma mudança seguinte (caminho provável: hook `async_filter_listed_models` do LiteLLM e/ou overrides por principal do LibreChat).
- Cotas, budgets e rate limits (Fase 4); classificação de conteúdo (Fases 5–6); modo AUTO (Fase 7).
- Integração com AD/LDAP/SSO e gestão de grupos por interface gráfica.
- Chaves de API individuais por usuário para uso direto do gateway.

### Impacto em segurança

É a primeira barreira de autorização da plataforma, e falha de forma conservadora: na dúvida (política ilegível, usuário sem identidade, modelo desconhecido), nega. Limitação conhecida e aceita nesta fase: a identidade vem de um cabeçalho enviado pelo portal com a chave virtual dele. Quem tivesse essa chave poderia se passar por outro usuário, mas ela existe só no container do LibreChat e o gateway só é publicado em `127.0.0.1`. O endurecimento fica para a Fase 10.

## Capabilities

### New Capabilities

- `model-access`: política de acesso a modelos por usuário e grupo, aplicada no gateway antes de qualquer chamada, com negação HTTP 403 `MODEL_ACCESS_DENIED`, comportamento fail-closed e recarga da política sem reinício.

### Modified Capabilities

- `chat-portal`: o requisito "Propagação de identidade" passa a incluir o e-mail do usuário autenticado, usado pelo gateway para autorização.

## Impact

- **Novos arquivos**: `litellm/custom/model_access.py`, `litellm/policies/model-access.yaml`, `tests/smoke/model-access.sh`, `docs/models.md`.
- **Alterados**: `litellm/config.yaml` (registro do callback), `docker-compose.yml` (montagem da política), `librechat/librechat.yaml` (cabeçalho de e-mail), `tests/smoke/gateway.sh` (identidade nas chamadas), `docs/architecture.md`, `docs/testing.md`, `README.md`.
- **Comportamento**: requisições ao gateway com a chave do portal passam a exigir identidade; usuários fora da política caem no grupo padrão.
- **Dependências**: nenhuma nova; usa a interface `CustomLogger` do LiteLLM v1.104.0.
