# Tasks

## 1. Fase 0 — Baseline e estrutura do repositório

- [x] 1.1 Criar `docs/architecture.md` com a arquitetura alvo, a tabela de versões/imagens do `design.md`, as redes (D2), o aviso de que `gemini` ainda não tem filtro de segurança e links para a documentação oficial; verificar que cada versão citada confere com `docker manifest inspect` da imagem correspondente
- [x] 1.2 Criar `.gitignore` (incluindo `.env`, dados locais e logs) e a estrutura de diretórios `librechat/`, `litellm/custom/`, `local-ai/`, `postgres/`, `scripts/`, `tests/smoke/`, `docs/`; verificar com `git status --ignored` que `.env` é ignorado
- [x] 1.3 Fixar as tags pendentes (Redis patch, PostgreSQL patch, digest do MinIO) e registrá-las em `docs/architecture.md`; verificar que nenhuma imagem do compose usa `latest`, `main-stable` ou `dev` (`grep -nE 'image: *[^@]+:(latest|main-stable|stable|dev)$' docker-compose*.yml` sem resultados; imagens fixadas por digest `@sha256:` são aceitas)

## 2. Fase 1 — Dados, observabilidade e Presidio

- [x] 2.1 Criar `.env.example` com todas as variáveis da stack, sem valores reais, e instruções para gerar secrets (`openssl rand -hex 32`); verificar que `docker compose config` falha indicando a variável ausente quando um secret obrigatório não está definido
- [x] 2.2 Declarar no `docker-compose.yml` as redes do D2 (`internal: true` exceto `edge`) e os volumes nomeados; verificar com `docker compose config` que o arquivo é válido
- [x] 2.3 Adicionar `postgres` com script `postgres/init/` que cria os bancos/usuários `langfuse` e `litellm`, sem porta publicada; verificar com `docker compose up -d postgres` + `psql -l` dentro do container que os dois bancos existem
- [x] 2.4 Adicionar `clickhouse`, `redis`, `minio`, `langfuse-worker` e `langfuse-web` conforme compose oficial do Langfuse (D7), com secrets obrigatórios, telemetria desligada e `LANGFUSE_INIT_*`; verificar que todos ficam `healthy` e que o login com o usuário inicial funciona em `http://localhost:${LANGFUSE_PORT}`
- [x] 2.5 Adicionar `presidio-analyzer` e `presidio-anonymizer` em `ai-net` com health check; verificar de dentro da rede (`docker compose exec litellm` ou container temporário em `ai-net`) que `GET /health` responde 200 e que `POST /analyze` detecta um e-mail de exemplo

## 3. Fase 1 — IA Local

- [x] 3.1 Adicionar `local-ai-pull` (one-shot, rede `edge`) e `local-ai` (somente `ai-net`) com volume `ollama-models` e `LOCAL_AI_MODEL` (padrão `qwen2.5:3b`), conforme D3; verificar que na primeira subida o modelo é baixado e na segunda `local-ai-pull` termina sem novo download
- [x] 3.2 Configurar health check de `local-ai` com `ollama show $LOCAL_AI_MODEL`; verificar que o serviço só fica `healthy` após o modelo estar disponível
- [x] 3.3 Verificar o modo CPU: com o `docker-compose.yml` base, `local-ai` sobe `healthy` e responde a um chat de teste
- [x] 3.4 Criar `docker-compose.gpu.yml` (reserva de GPU NVIDIA para `local-ai`, `OLLAMA_CONTEXT_LENGTH` configurável) e documentar `COMPOSE_FILE` no `.env.example`; verificar com `docker compose config` que a reserva aparece só com o override
- [x] 3.5 **Pré-requisito do usuário** (concluído em 06/10/2026: runtime `nvidia` presente, `nvidia-smi` no container mostra a RTX 3050 Ti): instalar `nvidia-container-toolkit` 1.20.1 conforme o guia oficial da NVIDIA e reiniciar o Docker; verificar com `docker info` que o runtime `nvidia` aparece e com `docker run --rm --gpus all ubuntu nvidia-smi` que a GPU é visível
- [x] 3.6 Habilitar o modo GPU e verificar que `ollama ps` dentro de `local-ai` mostra o modelo `100% GPU` e que a latência de um chat de teste cai em relação ao modo CPU; registrar os números em `docs/testing.md` e os passos de instalação do toolkit em `docs/architecture.md`

## 4. Fase 1/2 — AI Gateway (LiteLLM)

- [x] 4.1 Criar `litellm/config.yaml` com `model_list` (`local-ai` via `ollama_chat` e `gemini` via `gemini/${GEMINI_MODEL}`), `master_key` e `database_url` por variável de ambiente (D4); verificar que `docker compose config` não expõe valores reais versionados
- [x] 4.2 Adicionar o serviço `litellm` (redes `edge`, `frontend-net`, `ai-net`, `observability-net`, `data-net`; porta só em `127.0.0.1:4000`; `./litellm/custom` montado read-only; `STORE_MODEL_IN_DB=False`); verificar `GET /health/liveliness` = 200 e serviço `healthy`
- [x] 4.3 Configurar `GEMINI_MODEL=gemini-3-flash-preview` (já validado direto na API do Google) e registrar a escolha e a alternativa estável `gemini-3.8-flash` em `docs/architecture.md`; verificar com uma chamada `model: gemini` via gateway que retorna resposta válida
- [x] 4.4 Criar o serviço one-shot `litellm-bootstrap` que gera a chave virtual `LITELLM_PORTAL_KEY` (`key_alias: librechat-portal`) via `POST /key/generate`, idempotente; verificar que roda duas vezes seguidas sem erro e que a chave autentica em `GET /v1/models`
- [x] 4.5 Criar `tests/smoke/gateway.sh` cobrindo: `/v1/models` lista `local-ai` e `gemini`; 401 sem chave; chat `local-ai` com `usage` > 0; streaming SSE terminando em `[DONE]`; chat `gemini` (pulado com aviso se a chave estiver vazia); erro explícito (sem fallback silencioso) com chave Gemini inválida; uso persistido com o end-user enviado em `x-litellm-end-user-id`; verificar executando o script com todos os casos aprovados

## 5. Fase 2 — Portal (LibreChat)

- [x] 5.1 Adicionar `mongodb` e `meilisearch` em `portal-data-net` com volumes nomeados e health checks; verificar que ficam `healthy`
- [x] 5.2 Criar `librechat/librechat.yaml` (config `1.3.17`) com endpoint custom único apontando para `http://litellm:4000/v1`, `apiKey: ${LITELLM_PORTAL_KEY}`, `models.fetch: true`, `titleModel: local-ai` e cabeçalhos `x-litellm-end-user-id`/`x-litellm-session-id` (D5); verificar que o LibreChat inicia sem erros de validação de config nos logs
- [x] 5.3 Adicionar o serviço `librechat` (redes `edge`, `frontend-net`, `portal-data-net`; `ENDPOINTS=custom`; registro e login social desligados; sem nenhuma chave de provedor no ambiente), dependente de `litellm-bootstrap` concluído; verificar `healthy` e que `docker compose exec librechat env` não contém chaves de provedor
- [x] 5.4 Criar `scripts/create-user.sh` (wrapper de `npm run create-user`) e criar um usuário de teste; verificar login no navegador em `http://localhost:${LIBRECHAT_PORT}` e que visitante não autenticado é redirecionado ao login
- [x] 5.5 Validar no navegador: seletor mostra só o endpoint do gateway com `local-ai` e `gemini`; conversa com cada modelo exibe streaming; conversa reaberta mantém contexto; segundo usuário não vê conversas do primeiro; com `litellm` parado, o portal exibe erro. Registrar evidências em `docs/testing.md`
- [x] 5.6 Verificar no LiteLLM (spend logs) que as requisições feitas pelo portal registram o ID do usuário e da conversa do LibreChat; registrar em `docs/testing.md`

## 6. Integração, isolamento e documentação de uso

- [x] 6.1 Criar `scripts/healthcheck.sh` que falha se algum serviço não estiver `healthy` ou se houver porta publicada além de LibreChat, Langfuse e `127.0.0.1:4000`; verificar executando com a stack no ar
- [x] 6.2 Criar `tests/smoke/network-isolation.sh`: a partir de `librechat`, `local-ai:11434`, `presidio-analyzer:3000`, `postgres:5432` e `clickhouse:8123` são inalcançáveis; a partir de `local-ai`, a internet é inalcançável; verificar executando o script com todos os casos aprovados
- [x] 6.3 Escrever `README.md` (pré-requisitos, `cp .env.example .env`, geração de secrets, `docker compose up -d`, criação de usuário, testes, aviso de segurança sobre `gemini` sem filtro, requisitos de memória e tempo da primeira subida) e `docs/testing.md` (como rodar cada teste); verificar seguindo o README a partir de `docker compose down -v` até uma conversa funcional
- [x] 6.4 Validação final: `docker compose down` + `up -d` preserva usuários, conversas e modelo baixado; `scripts/healthcheck.sh` e todos os `tests/smoke/*.sh` passam; registrar resultado real (sem simulação) e pendências em `docs/testing.md`

## Workflow follow-up

- Revisar os artefatos e iniciar a implementação com `/opsx:apply` (ou pedindo para aplicar a mudança).
- Fazer commits lógicos ao fim de cada grupo de tarefas.
- Arquivar a mudança com `/opsx:archive` após a validação final, antes de iniciar a Fase 3.
