# Design

## Context

Estado atual (specs `model-access` e `chat-portal`): o hook `litellm/custom/model_access.py` bloqueia chamadas com base no cabeçalho `x-corporate-user-email`; o portal também envia `x-litellm-end-user-id` com o ID interno do LibreChat. A listagem `/v1/models` não é filtrada.

Investigação feita para esta mudança (06/10/2026):

**LibreChat v0.8.8** (`/app/packages/api/dist/index.cjs` na imagem):
- `loadConfigModels` chama `fetchModels` a cada requisição de `/api/models`, passando `headers` do endpoint e `userObject`. Não há cache acima disso (`api/server/controllers/ModelController.js`).
- Em `fetchModels`, `hasUserScopedHeaders` (cabeçalhos configurados mais usuário) **desliga o cache compartilhado** (`MODEL_QUERIES`). Os cabeçalhos são resolvidos por usuário (`resolveHeaders`, com `{{LIBRECHAT_USER_EMAIL}}` etc.) e enviados no `GET /models`.
- Se a busca falha ou volta vazia, o seletor usa `models.default` do `librechat.yaml`.

**LiteLLM v1.104.0**:
- `CustomLogger.async_filter_listed_models(user_api_key_dict, model_names)` é aplicado em `/v1/models`, `/v1/models/{id}`, `/model/info` e `/model_group/info`. Um nome omitido some da listagem, e a consulta individual responde 404.
- O hook não recebe os cabeçalhos, mas `user_api_key_dict.end_user_id` é preenchido na autenticação de **todas** as rotas a partir de `get_end_user_id_from_request_body` (cabeçalhos padrão `x-litellm-end-user-id`/`x-litellm-customer-id` primeiro, depois o campo `user` do corpo).
- `resolve_and_validate_end_user_id` repassa qualquer texto quando `validate_end_user_id_in_db` está desligado (padrão). Um e-mail é aceito sem cadastro prévio.
- Teste empírico em container descartável: com `x-litellm-end-user-id: bruno@x` o filtro recebeu `end_user_id='bruno@x'` e a listagem devolveu só `local-ai`.
- Constatado na implementação: para chaves que não são de administrador, a rota `/v1/models/{id}` é bloqueada inteira pelo LiteLLM (`route_checks.py`: HTTP 401 "Only proxy admin…") para **qualquer** id, inclusive inexistente. Com a chave do portal, consultar `gemini` dá a mesma resposta que consultar um modelo que não existe; o modelo oculto não é revelado. Com chave mestra (admin), o filtro não se aplica. O teste empírico com a chave mestra antes do bypass de admin respondeu 404 para o modelo filtrado.

## Goals / Non-Goals

**Goals:**
- Uma única identidade de usuário final, nativa do LiteLLM, para bloquear chamadas, filtrar a listagem e (Fase 4) aplicar cotas por usuário.
- Seletor do LibreChat coerente com a política, sem código no LibreChat.

**Non-Goals:**
- Alterar a lógica de grupos ou o formato da política.
- Tratar conversas antigas que referenciam um modelo hoje negado.

## Decisions

### D1. Identidade = `end_user_id` do LiteLLM, preenchido com o e-mail
- `librechat.yaml`: `x-litellm-end-user-id: '{{LIBRECHAT_USER_EMAIL}}'` e remoção de `x-corporate-user-email`.
- O hook passa a ler `user_api_key_dict.end_user_id` (normalizado em minúsculas) nas duas operações; não lê mais cabeçalhos do `data`.
- Precedência do LiteLLM: o cabeçalho padrão vence o campo `user` do corpo. O LibreChat envia `user: <ID interno>` no corpo das mensagens, mas o cabeçalho com o e-mail prevalece. A implementação deve confirmar isso no registro de uso.
- *Alternativa*: manter `x-corporate-user-email` para o bloqueio e usar `end_user_id` só na listagem. Rejeitada: duas identidades que podem divergir.
- *Alternativa*: mapear o ID interno do LibreChat para e-mail no gateway. Rejeitada: exigiria acesso do gateway ao MongoDB do portal, o que quebra o isolamento de redes.

### D2. Filtro de listagem no mesmo `ModelAccessControl`
`async_filter_listed_models` no mesmo handler:
- chave mestra → todos;
- sem `end_user_id` → `[]`;
- política indisponível → `[]`;
- caso contrário → interseção com os modelos permitidos.

A lógica de permissão (`Policy.allowed_models`) é a mesma do bloqueio, então as duas operações não divergem.

### D3. Lista de reserva do portal
`models.default: ['local-ai']`. Se o gateway falhar na listagem, ou devolver vazio para alguém sem identidade, o seletor mostra só a IA Local, que todos os grupos permitem (requisito "Falha na busca de modelos").

### D4. Testes
- Unitários: filtro (parcial, sem identidade, política indisponível, admin, preserva a ordem); bloqueio lendo `end_user_id`.
- `tests/smoke/model-access.sh`: cabeçalho trocado para `x-litellm-end-user-id`. Novos casos de listagem: Bruno → `[local-ai]`, Ana → ambos, sem identidade → `[]`, mestre → ambos, `GET /v1/models/gemini` como Bruno → mesma resposta que `GET /v1/models/<inexistente>`, política inválida → `[]`.
- `tests/smoke/gateway.sh`: identidade = `<run>@smoke.local` em `x-litellm-end-user-id`; a checagem do registro de uso passa a procurar esse e-mail.
- Navegador: seletor do Bruno só com `local-ai`, da Ana com ambos; registro de uso com e-mail no `end_user`.

## Risks / Trade-offs

- **`end_user` dos registros muda de formato** (ID → e-mail) a partir desta mudança → documentado; registros antigos mantêm o ID.
- **Sem cache, cada abertura do seletor consulta o gateway** → custo desprezível (listagem local, sem chamada a provedor).
- **Corpo com `user` diferente do cabeçalho**: o cabeçalho prevalece, conforme a ordem de precedência do LiteLLM. Se uma versão futura inverter essa ordem, a identidade viraria o ID interno e os usuários cairiam no grupo padrão → os testes de fumaça verificam o `end_user` gravado e acusariam a mudança.
- **E-mail (dado pessoal) nos registros de uso** → é exatamente o que a auditoria corporativa pede ("quem perguntou"); acesso aos registros restrito ao gateway/admin.

## Migration Plan

Atualizar o hook e o `librechat.yaml`, recriar `litellm` e `librechat`. Rollback: restaurar os dois arquivos e recriar os serviços.
