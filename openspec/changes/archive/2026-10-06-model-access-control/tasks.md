# Tasks

## 1. Política e hook de autorização

- [x] 1.1 Criar `litellm/policies/model-access.yaml` com os grupos iniciais, o grupo padrão `USER` e os usuários de teste (Ana → DEVELOPER, Bruno → FINANCE), conforme D2; verificar que o arquivo é YAML válido com `python3 -c "import yaml,sys;yaml.safe_load(open(sys.argv[1]))"`
- [x] 1.2 Implementar `litellm/custom/model_access.py` (carga, validação e recarga por mtime da política; resolução de grupos; classificação da chave; negação HTTP 403 `MODEL_ACCESS_DENIED`; log das negações), conforme D1–D6; verificar que o módulo importa sem erro dentro do container `litellm`
- [x] 1.3 Criar `tests/unit/test_model_access.py` cobrindo união de grupos, grupo padrão, e-mail em maiúsculas, política inválida, versão errada, grupo inexistente e modelo fora da política; verificar executando com `docker compose exec -T litellm python3` e obtendo todos os casos aprovados

## 2. Integração no gateway e no portal

- [x] 2.1 Registrar o callback em `litellm/config.yaml` e montar `./litellm/policies` em `/app/policies` (somente leitura) com `MODEL_ACCESS_POLICY_PATH` no `docker-compose.yml`; verificar que o `litellm` fica `healthy` e que o log de inicialização mostra a política carregada
- [x] 2.2 Confirmar de onde o hook lê os cabeçalhos (`proxy_server_request.headers` ou `metadata.headers`) com uma requisição real e ajustar o hook se necessário; verificar que uma chamada com `x-corporate-user-email` de Bruno para `gemini` retorna 403 e para `local-ai` retorna 200
- [x] 2.3 Adicionar o cabeçalho `x-corporate-user-email: '{{LIBRECHAT_USER_EMAIL}}'` em `librechat/librechat.yaml` e recriar o `librechat`; verificar no log do gateway (ou no hook) que as requisições do portal chegam com o e-mail do usuário logado
- [x] 2.4 Atualizar `tests/smoke/gateway.sh` para enviar `x-corporate-user-email`; verificar que o script volta a passar integralmente

## 3. Verificação ponta a ponta

- [x] 3.1 Criar `tests/smoke/model-access.sh` cobrindo D7 (permitido, negado, chamada direta, sem identidade → 403, e-mail desconhecido → grupo padrão, chave mestra, recarga a quente com restauração via `trap`, política inválida → nega); verificar executando o script com todos os casos aprovados e a política restaurada ao final (`git diff --exit-code litellm/policies`)
- [x] 3.2 Verificar que as negações aparecem no log do gateway com usuário, modelo e motivo, e conferir o que fica registrado em `LiteLLM_SpendLogs`; registrar em `docs/testing.md`
- [x] 3.3 Validar no navegador: Bruno (FINANCE) seleciona `gemini` → portal exibe erro e não gera resposta; Bruno com `local-ai` → funciona; Ana (DEVELOPER) com `gemini` → funciona. Registrar o texto de erro exibido e as evidências em `docs/testing.md`

## 4. Documentação

- [x] 4.1 Criar `docs/models.md` (modelos, grupos, como adicionar usuário ou grupo, recarga sem reinício, comportamento fail-closed, como validar o YAML) e atualizar `docs/architecture.md` (hook de ACL, cabeçalho de e-mail) e `README.md` (gestão de acesso); verificar seguindo o próprio `docs/models.md` para adicionar um usuário de teste a um grupo e confirmar o efeito sem reiniciar o gateway
- [x] 4.2 Rodar `scripts/healthcheck.sh`, `tests/smoke/gateway.sh`, `tests/smoke/model-access.sh` e `tests/smoke/network-isolation.sh`; verificar que todos passam e registrar o resultado em `docs/testing.md`

## Workflow follow-up

- Revisar os artefatos e iniciar a implementação com `/opsx:apply` (ou pedindo para aplicar a mudança).
- Arquivar com `/opsx:archive` após a validação; em seguida, planejar a visibilidade de modelos no seletor (RF-002) e a Fase 4 (cotas e budgets).
