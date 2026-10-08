# Design

## Context

Projeto greenfield: o repositório só tem a especificação (ver `proposal.md` — Why). Restrições do host verificadas em 06/10/2026:

- Docker 29.1.3 e Compose 2.40.3; 16 vCPU, 61 GB RAM, 1,7 TB livres.
- GPU NVIDIA RTX 3050 Ti Laptop (4 GB VRAM), driver 595.91.07, Ubuntu 26.04.1. `nvidia-container-toolkit` 1.20.1-1 instalado pelo usuário em 06/10/2026 a partir do repositório oficial da NVIDIA (docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html); `docker info` lista o runtime `nvidia` e `docker run --rm --gpus all ubuntu nvidia-smi` enxerga a GPU (CUDA 13.2).
- A porta 5432 do host já está em uso por outro projeto (`cronicas-local-db-1`).

Versões selecionadas (Fase 0), confirmadas nos releases oficiais e com tag de imagem verificada por `docker manifest inspect`:

| Componente | Versão | Imagem | Referência |
|---|---|---|---|
| LibreChat | v0.8.8 (01/10/2026) | `registry.librechat.ai/librechat-ai/librechat:v0.8.8` | github.com/LibreChat-AI/LibreChat, `docker-compose.yml` oficial |
| MongoDB (LibreChat) | 8.0.20 | `mongo:8.0.20` | idem |
| Meilisearch (LibreChat) | v1.35.1 | `getmeili/meilisearch:v1.35.1` | idem |
| LiteLLM Proxy | v1.104.0 (03/10/2026) | `docker.litellm.ai/berriai/litellm:v1.104.0` | docs.litellm.ai/docs/proxy/docker_quick_start |
| Langfuse web/worker | 4.53.0 (06/10/2026) | `docker.langfuse.com/langfuse/langfuse[-worker]:4.53.0` | langfuse.com/self-hosting/deployment/docker-compose |
| ClickHouse | 25.12 | `clickhouse/clickhouse-server:25.12` | `docker-compose.yml` oficial do Langfuse |
| Redis | 7 | `redis:7` (fixar patch na implementação) | idem |
| MinIO | — | `cgr.dev/chainguard/minio` (fixar digest) | idem |
| PostgreSQL | 17 | `postgres:17` (fixar patch) | idem |
| Presidio analyzer/anonymizer | 2.2.362 | `mcr.microsoft.com/presidio-{analyzer,anonymizer}:2.2.362` | microsoft.github.io/presidio/installation |
| Ollama | 0.40.0 | `ollama/ollama:0.40.0` | docs.ollama.com/openai |

Observação: o release mais recente do Presidio é 2.2.364, mas a última imagem publicada no MCR é 2.2.362 — usamos a imagem disponível.

## Goals / Non-Goals

**Goals:**
- Compor a stack a partir dos `docker-compose.yml` oficiais de cada produto, sem fork e sem código próprio além de configuração e scripts de bootstrap/teste.
- Deixar já posicionados os pontos de extensão das próximas fases: diretório `litellm/custom/` montado no gateway, identidade do usuário/conversa chegando ao LiteLLM, rede de observabilidade ligando LiteLLM ↔ Langfuse.

**Non-Goals:**
- Integrar o callback de Langfuse no LiteLLM (Fase 8): exige validar a compatibilidade do callback do LiteLLM v1.104 com o Langfuse v4 — não fazemos isso agora.
- RAG do LibreChat (`rag_api` + `vectordb` do compose oficial): fora do escopo da POC; omitidos.
- Painel `admin-panel` do LibreChat: omitido (conflita com a porta 3000 do Langfuse e não é necessário).

## Decisions

### D1. Um único `docker-compose.yml` próprio, montado a partir dos oficiais
Copiamos apenas os serviços necessários dos compose oficiais (LibreChat, Langfuse, LiteLLM) para um compose único, preservando imagens, variáveis e health checks oficiais. `docker-compose.dev.yml` fica reservado para overrides de desenvolvimento (ex.: publicar portas extras).
- *Alternativa*: `include:` dos compose upstream — rejeitada; eles usam tags móveis (`latest`, `main-stable`), publicam bancos no host (5432 conflita) e não têm a segmentação de redes exigida.

### D2. Redes segmentadas com `internal: true`

| Rede | Tipo | Membros |
|---|---|---|
| `edge` | bridge com saída | `librechat`, `langfuse-web`, `litellm` (único com saída à internet para provedores) |
| `frontend-net` | internal | `librechat`, `litellm` |
| `portal-data-net` | internal | `librechat`, `mongodb`, `meilisearch` |
| `ai-net` | internal | `litellm`, `local-ai`, `presidio-analyzer`, `presidio-anonymizer` |
| `observability-net` | internal | `litellm`, `langfuse-web`, `langfuse-worker` |
| `data-net` | internal | `litellm`, `langfuse-web`, `langfuse-worker`, `postgres`, `clickhouse`, `redis`, `minio` |

O Docker não publica portas de containers ligados somente a redes `internal`; por isso os serviços com porta publicada (`librechat`, `langfuse-web`, `litellm` em loopback) também participam de `edge`. Atende ao requisito de que o portal não alcance IA Local, Presidio nem bancos de terceiros.
- *Alternativa*: rede única — rejeitada pelo requisito de isolamento (doc. de software §11.2).

### D3. IA Local = Ollama (CPU por padrão, GPU via override), com download do modelo em serviço one-shot
- `local-ai-pull`: mesma imagem `ollama/ollama:0.40.0`, conectado a `edge`, monta o volume `ollama-models`, executa `ollama serve` em background + `ollama pull $LOCAL_AI_MODEL` e termina. É idempotente (não baixa de novo se já existe).
- `local-ai`: mesmo volume, somente em `ai-net` (sem saída), `depends_on: local-ai-pull: condition: service_completed_successfully`. Health check: `ollama show $LOCAL_AI_MODEL` (a imagem não tem `curl`).
- A API OpenAI-compatible do Ollama é usada em `http://local-ai:11434` (`/v1/chat/completions`, `/v1/models`).
- Modelo padrão: `qwen2.5:3b` (~2 GB, bom português, sem modo "thinking"), configurável via `LOCAL_AI_MODEL`. `qwen3:4b` fica documentado como alternativa mais forte para a futura classificação (Fase 6).
- *Alternativas*: vLLM (exige GPU), Ollama com saída à internet permanente (violaria o princípio de que a IA Local não sai da rede interna).
- GPU: o `docker-compose.yml` base é CPU-only (sobe em qualquer host). Um override `docker-compose.gpu.yml` adiciona ao `local-ai` a reserva `deploy.resources.reservations.devices` (`driver: nvidia`, `count: 1`, `capabilities: [gpu]`) — sintaxe oficial do Compose para GPU. Ativado no `.env` com `COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml`, de modo que `docker compose up -d` continua sendo o único comando. Neste host será o modo padrão após a instalação do toolkit.
- VRAM de 4 GB: `qwen2.5:3b` (Q4, ≈2 GB) ou `qwen3:4b` (≈2,5 GB) cabem inteiros na GPU com contexto moderado; modelos ≥7B não cabem e o Ollama divide entre GPU e CPU (mais lento). Contexto limitado via `OLLAMA_CONTEXT_LENGTH` para não estourar a VRAM.
- *Alternativa*: GPU obrigatória no compose base — rejeitada; impediria subir a stack em hosts sem GPU (ex.: CI) e violaria o requisito de execução em CPU.

### D4. LiteLLM: config em arquivo + PostgreSQL para uso/custos
- `litellm/config.yaml` define `model_list` com nomes lógicos:
  - `local-ai` → `ollama_chat/${LOCAL_AI_MODEL}`, `api_base: http://local-ai:11434`, custo zero.
  - `gemini` → `gemini/${GEMINI_MODEL}` com `api_key: os.environ/GEMINI_API_KEY`.
- `general_settings.master_key: os.environ/LITELLM_MASTER_KEY` e `database_url` apontando para o PostgreSQL compartilhado (D6), necessário para chaves virtuais e spend logs.
- `STORE_MODEL_IN_DB=False`: o catálogo é versionado no arquivo, não editável pela UI.
- Volume `./litellm/custom:/app/custom:ro` já montado (vazio nesta fase) para o Security Router.
- Health check: `/health/liveliness` (padrão do compose oficial). Porta publicada apenas em `127.0.0.1:4000` para testes de fumaça.

### D5. LibreChat → LiteLLM com chave virtual dedicada e identidade em cabeçalhos
- `ENDPOINTS=custom` desabilita os endpoints nativos de provedores; `librechat.yaml` (versão de config `1.3.17`) declara um único endpoint custom `Corporate AI` com `baseURL: http://litellm:4000/v1`, `models.fetch: true` e `titleModel: local-ai` (gerar títulos não deve enviar conteúdo para fora).
- Cabeçalhos enviados (placeholders oficiais do LibreChat): `x-litellm-end-user-id: {{LIBRECHAT_USER_ID}}` e `x-litellm-session-id: {{LIBRECHAT_BODY_CONVERSATIONID}}`. Ambos são reconhecidos nativamente pelo LiteLLM v1.104 (`STANDARD_CUSTOMER_ID_HEADERS` em `litellm/constants.py`; `_EXPLICIT_SESSION_HEADERS` em `litellm/proxy/litellm_pre_call_utils.py`).
- A chave usada pelo LibreChat (`LITELLM_PORTAL_KEY`) **não é a master key**: um serviço one-shot `litellm-bootstrap` chama `POST /key/generate` com `key: $LITELLM_PORTAL_KEY` (campo documentado: "User defined key value. Must start with 'sk-'") e `key_alias: librechat-portal`; se a chave já existir, trata como sucesso.
- Registro: `ALLOW_REGISTRATION=false`, `ALLOW_SOCIAL_LOGIN=false`; usuários de teste criados com `docker compose exec librechat npm run create-user`. Uploads de arquivo funcionam sem RAG apenas para modelos que aceitam o conteúdo inline; limitação documentada.
- *Alternativa*: usar a master key no portal — rejeitada por dar ao portal poder administrativo sobre o gateway.

### D6. PostgreSQL compartilhado com separação lógica
Uma instância `postgres:17` em `data-net` com dois bancos e usuários distintos (`langfuse`, `litellm`), criados por script em `/docker-entrypoint-initdb.d`. Nenhuma porta publicada no host (evita o conflito em 5432).
- *Alternativa*: duas instâncias — mais isolamento, porém mais memória; revisitar no hardening.

### D7. Langfuse conforme compose oficial, sem conexão com o LiteLLM ainda
`langfuse-web`, `langfuse-worker`, `clickhouse`, `redis`, `minio` com as variáveis do compose oficial; todas as entradas marcadas `# CHANGEME` passam a ser obrigatórias no `.env` (sintaxe `${VAR:?mensagem}`), sem valor padrão. `TELEMETRY_ENABLED=false`. Projeto/usuário inicial criados via variáveis `LANGFUSE_INIT_*`, já gerando as chaves que a Fase 8 usará. Apenas `langfuse-web` publica porta (`LANGFUSE_PORT`, padrão 3000). O endpoint de upload de mídia do MinIO não é publicado; upload de mídia pelo navegador no Langfuse fica indisponível nesta fase.

### D8. Validação executável
- `scripts/healthcheck.sh`: verifica que todos os serviços estão `healthy` e que as portas publicadas são somente as esperadas.
- `tests/smoke/` (shell + `curl`, executável do host contra `127.0.0.1:4000`): `/v1/models`, chat `local-ai` (normal e streaming), chat `gemini` (pulado com aviso se `GEMINI_API_KEY` estiver vazia), 401 sem chave, uso de tokens presente, isolamento de rede (`docker compose exec librechat` tentando alcançar `local-ai:11434` deve falhar).
- Fluxo pelo LibreChat validado manualmente no navegador (login, conversa, streaming, histórico) e registrado em `docs/testing.md`.

## Risks / Trade-offs

- **Prompts para `gemini` saem sem filtro** → aviso explícito no README e no `docs/architecture.md`; ambiente restrito a desenvolvimento até a Fase 5.
- **Inferência em CPU lenta** (modelo 3B: alguns tokens/s) → com o override de GPU a RTX 3050 Ti atende; sem ela, modelo pequeno por padrão e timeouts do LiteLLM ajustados para `local-ai`.
- **VRAM de 4 GB compartilhada com o desktop** → limitar contexto; verificar com `nvidia-smi` que o modelo carregou 100% na GPU (`ollama ps` mostra `100% GPU`).
- **`{{LIBRECHAT_BODY_CONVERSATIONID}}` pode chegar como `new` na primeira mensagem** e o LiteLLM só aceita session IDs com 8+ caracteres alfanuméricos → aceitável nesta fase (a sessão fica sem ID nessa mensagem); revisar na Fase 8.
- **Cabeçalho de identidade é confiável apenas porque só o LibreChat alcança o gateway** pela rede interna e com chave própria; o gateway publicado em `127.0.0.1` aceita qualquer `x-litellm-end-user-id` de quem tem a chave → endurecer na Fase 3 (identidade vinculada à chave/JWT).
- **Consumo de memória** da stack completa (≈8–12 GB, ClickHouse e Langfuse são os maiores) → aceitável no host atual; documentar requisitos mínimos.
- **Imagens com tag `7`/`17`/sem tag (MinIO)** no compose oficial do Langfuse → fixar patch/digest na implementação para cumprir o requisito de imagens versionadas.
- **Primeira subida demorada** (download de imagens ≈ vários GB + modelo ≈ 2 GB) → documentado no README.

## Migration Plan

Não há sistema anterior. Rollback: `docker compose down` (preserva volumes) ou `docker compose down -v` (remove dados). Atualizações futuras de versão alteram apenas as tags no compose e são validadas pelos mesmos testes de fumaça.

## Open Questions

Nenhuma. `GEMINI_MODEL` foi definido como `gemini-3-flash-preview` (escolha do usuário em 06/10/2026), validado com `generateContent` na chave fornecida. Por ser um modelo *preview*, pode ser descontinuado; alternativa estável disponível na mesma chave: `gemini-3.8-flash`. Modelos 2.5 já não estão disponíveis para novos usuários (HTTP 404).
