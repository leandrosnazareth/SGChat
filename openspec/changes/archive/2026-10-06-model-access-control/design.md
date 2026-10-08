# Design

## Context

Estado atual (specs `ai-gateway` e `chat-portal`): o LibreChat chama o LiteLLM v1.104.0 com uma única chave virtual (`librechat-portal`) e envia `x-litellm-end-user-id` (ID do usuário no LibreChat) e `x-litellm-session-id`. Não existe nenhuma restrição de modelo por usuário.

Investigação no código do LiteLLM v1.104.0 (imagem `docker.litellm.ai/berriai/litellm:v1.104.0`):

- As restrições nativas de modelo (`models` em chaves, usuários e times) são avaliadas **pela chave** durante a autenticação. Com uma chave compartilhada pelo portal, elas não distinguem usuários.
- `general_settings.user_header_mappings` com papel `INTERNAL_USER` apenas define `user_id` em `add_internal_user_from_user_mapping` (`litellm/proxy/litellm_pre_call_utils.py`), **depois** da autenticação: serve para atribuição de uso, não para ACL.
- `CustomLogger.async_pre_call_hook(user_api_key_dict, cache, data, call_type)` roda antes da chamada ao provedor e pode rejeitar a requisição levantando exceção.
- Callbacks do `config.yaml` (`litellm_settings.callbacks: <módulo>.<instância>`) são carregados relativos ao diretório do config (`get_instance_fn` em `litellm/proxy/types_utils/utils.py`): `custom.model_access.handler` → `/app/custom/model_access.py`.
- Teste empírico (container descartável): `HTTPException(status_code=403, detail={"error": "MODEL_ACCESS_DENIED", ...})` levantada no hook resulta em HTTP 403 com corpo `{"error":{"message":"MODEL_ACCESS_DENIED","type":"permission_error","code":"403",...}}`.
- Existe também `CustomLogger.async_filter_listed_models`, que filtra `/v1/models`: candidato para a mudança futura de visibilidade (fora do escopo).

Decisões do usuário (06/10/2026): fonte da verdade = arquivo YAML versionado; nesta fase apenas bloquear no gateway, sem esconder modelos no seletor.

## Goals / Non-Goals

**Goals:**
- Autorização por usuário e grupo aplicada no gateway, sem fork do LiteLLM, no mesmo ponto de extensão que o Security Router usará na Fase 5.
- Política legível e auditável (diff no git), recarregada sem reinício.

**Non-Goals:**
- Filtrar a listagem de modelos no portal ou no `/v1/models`.
- Gerenciar grupos via UI ou diretório corporativo.
- Ordenar ou combinar com futuras regras de segurança (o hook de ACL é independente; a ordem segurança → autorização será tratada na Fase 5).

## Decisions

### D1. Hook próprio `custom/model_access.py` (CustomLogger)
`ModelAccessControl(CustomLogger).async_pre_call_hook` avalia cada requisição de chamada a modelo e levanta `HTTPException(403, detail={"error": "MODEL_ACCESS_DENIED", "reason": ...})` ao negar. Registrado em `litellm_settings.callbacks: custom.model_access.handler`. O módulo é autocontido (sem imports de módulos irmãos), porque o carregador do LiteLLM importa o arquivo pelo caminho.
- *Alternativa*: chaves virtuais por usuário com `models` nativos e times como grupos. Rejeitada nesta fase: o LibreChat precisaria de uma chave por usuário (`user_provided`), uma experiência ruim, e a política ficaria no banco, não no YAML escolhido.

### D2. Formato da política
`litellm/policies/model-access.yaml`:

```yaml
version: 1
default_group: USER          # usuários não listados
groups:
  USER:           { models: [local-ai, gemini] }
  DEVELOPER:      { models: [local-ai, gemini] }
  FINANCE:        { models: [local-ai] }
  MANAGER:        { models: [local-ai, gemini] }
  ADMIN:          { models: [local-ai, gemini] }
  AUDITOR:        { models: [local-ai] }
  MASTER_AUDITOR: { models: [local-ai] }
users:
  ana.teste@corporate-ai.local:   [DEVELOPER]
  bruno.teste@corporate-ai.local: [FINANCE]
```

- Os grupos e permissões iniciais seguem a tabela §8 do documento de software, restrita aos modelos existentes hoje (`local-ai`, `gemini`). Os grupos de auditoria só recebem `local-ai`.
- E-mails comparados em minúsculas. Permissão efetiva = união dos grupos do usuário. Grupo inexistente referenciado → política inválida.
- Validação no carregamento: `version == 1`, `default_group` existe em `groups`, listas de modelos não vazias. Qualquer violação → política inválida → negar tudo (fail-closed) e logar o erro.
- Todos os grupos incluem `local-ai`, porque o portal gera os títulos das conversas com `local-ai` (`titleModel`); sem isso, a geração de título seria negada.

### D3. Identidade
- O LibreChat envia `x-corporate-user-email: '{{LIBRECHAT_USER_EMAIL}}'` (placeholder oficial), além dos cabeçalhos atuais.
- O hook lê os cabeçalhos de `data["proxy_server_request"]["headers"]`. Isso será confirmado na implementação; se o LiteLLM não expuser ali, o fallback é `data["metadata"]["headers"]`.
- Classificação da chave (`user_api_key_dict`):
  - `user_role == proxy_admin` (chave mestra) → administrativo, sem ACL (spec "Acesso administrativo");
  - qualquer outra chave → exige `x-corporate-user-email`; ausente → 403 (`reason: missing_identity`).
- Por que e-mail e não o ID do LibreChat: a política é escrita por pessoas; e-mail é legível e estável para usuários criados pelo administrador.

### D4. Montagem e recarga
- `./litellm/policies:/app/policies:ro` (diretório, não arquivo, para que edições com novo inode sejam vistas) e `MODEL_ACCESS_POLICY_PATH=/app/policies/model-access.yaml`.
- A cada requisição, o hook faz `os.stat` no arquivo; se `mtime`/tamanho mudaram, recarrega e revalida. O custo é desprezível perto de uma chamada de modelo.

### D5. Quais chamadas o hook avalia
Apenas `call_type` de geração (`completion`/`acompletion`, `text_completion`, `embeddings` etc.): o hook nega quando `data["model"]` não está no conjunto permitido. Listagens (`/v1/models`) não passam pelo hook e continuam mostrando todos os modelos (Non-Goal).

### D6. Registro das negações
Log estruturado (`logger corporate.model_access`, nível WARNING) com `user`, `model`, `reason` e `groups`, sem cabeçalhos de autorização. A exceção também gera o registro de falha padrão do LiteLLM em `LiteLLM_SpendLogs`; a implementação deve conferir que `end_user`/`model_group` aparecem nessa linha.

### D7. Testes
- `tests/unit/test_model_access.py`: avaliação da política (união de grupos, grupo padrão, e-mail em maiúsculas, política inválida → nega, modelo fora da política → nega), executado com `docker compose exec litellm python3`. Sem pytest: asserts simples, código de saída ≠ 0 em falha.
- `tests/smoke/model-access.sh`: contra o gateway real com a chave do portal:
  - Ana/DEVELOPER `gemini` → 200; Bruno/FINANCE `gemini` → 403 `MODEL_ACCESS_DENIED`; Bruno `local-ai` → 200;
  - e-mail desconhecido → grupo padrão; sem cabeçalho → 403; chave mestra → 200;
  - recarga a quente: o teste altera a política e restaura via `trap`;
  - política inválida → nega.
- `tests/smoke/gateway.sh` passa a enviar `x-corporate-user-email` (usuário de teste fora da política → grupo padrão `USER`).
- Navegador: Bruno escolhe `gemini` → erro exibido; Ana usa `gemini` normalmente.

## Risks / Trade-offs

- **Identidade por cabeçalho é confiável só porque apenas o portal tem a chave** → chave só no container do LibreChat, gateway em `127.0.0.1`. Na Fase 10, avaliar identidade assinada (JWT do LibreChat) ou chave por usuário.
- **Seletor mostra modelos que o usuário não pode usar** → erro claro no chat; visibilidade em mudança seguinte.
- **A mensagem de erro exibida pelo LibreChat pode ser genérica** ("The model provider could not complete this request") em vez de citar `MODEL_ACCESS_DENIED` → aceitável nesta fase; registrar o texto real observado em `docs/testing.md`.
- **Diferença de formato em relação ao `prompt.txt`**, que pede `{"error": "MODEL_ACCESS_DENIED"}` plano → adotamos o envelope OpenAI (`error.message = "MODEL_ACCESS_DENIED"`, `type = permission_error`, `code = "403"`), que os clientes OpenAI-compatible interpretam corretamente.
- **Editar o YAML com erro de sintaxe bloqueia todos os usuários** (fail-closed) → validação documentada em `docs/models.md` e mensagem clara no log.

## Migration Plan

Deploy: atualizar o compose e as configs e rodar `docker compose up -d litellm librechat`. Os usuários de teste existentes já estão na política. Usuários não listados passam ao grupo `USER` (`local-ai` + `gemini`), equivalente ao comportamento atual. Rollback: remover o callback do `config.yaml` e recriar o `litellm`.
