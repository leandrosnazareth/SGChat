# Proposal

## Why

O repositório hoje contém apenas a especificação (documento de software v1.0 e `prompt.txt`); nenhum componente da Corporate AI Platform existe ainda. Antes de implementar Security Router, cotas ou auditoria, é preciso uma base executável e validada: toda a stack em Docker e uma conversa real passando pelo gateway. Esta mudança cobre as **Fases 0, 1 e 2** do roadmap (`prompt.txt`, seção 28).

## What Changes

- **Fase 0 — baseline**: registrar em `docs/architecture.md` as versões selecionadas (LibreChat v0.8.8, LiteLLM v1.104.0, Langfuse 4.53.0, Presidio 2.2.362, Ollama 0.40.0), as dependências exigidas por cada produto e as decisões de arquitetura, citando a documentação oficial.
- **Fase 1 — infraestrutura Docker**: criar `docker-compose.yml`, `.env.example`, `README.md` e os arquivos de configuração (`librechat/librechat.yaml`, `litellm/config.yaml`) para subir LibreChat (+ MongoDB, Meilisearch), LiteLLM (+ PostgreSQL), Langfuse web/worker (+ PostgreSQL, ClickHouse, Redis, MinIO), Presidio analyzer/anonymizer e a IA Local (Ollama, acelerada pela GPU NVIDIA do host quando o runtime estiver instalado, com fallback para CPU), com redes segmentadas, health checks e o mínimo de portas expostas no host.
- **Fase 2 — fluxo mínimo de chat**: configurar o LiteLLM como único endpoint (OpenAI-compatible) conhecido pelo LibreChat, com dois modelos: `local-ai` (Ollama, GPU ou CPU) e `gemini` (Google Gemini via `GEMINI_API_KEY`). Validar streaming, histórico, contagem de tokens e tratamento de erro em conversas reais.
- Propagar desde já a identidade do usuário e o ID da conversa do LibreChat para o LiteLLM via cabeçalhos, preparando as fases de autorização e auditoria.
- Script de verificação (`scripts/healthcheck.sh` ou equivalente) e testes de fumaça executáveis que comprovem o funcionamento.

### Fora do escopo

- Security Router, Policy Engine, detecção de PII ativa no fluxo e classificação semântica (Fases 5–6). O Presidio apenas sobe e responde ao health check.
- Usuários/grupos, ACL de modelos e HTTP 403 (Fase 3); cotas, budgets e rate limits (Fase 4); modo AUTO (Fase 7).
- Integração LiteLLM → Langfuse e metadados de auditoria (Fase 8). O Langfuse apenas sobe saudável.
- OpenAI e Claude: podem ficar declarados como comentário/placeholder, mas não são configurados nem testados.
- TLS, hardening, backup e alta disponibilidade (Fase 10).

### Impacto em segurança

Nesta mudança **não existe** filtro de dados sensíveis: um prompt enviado ao modelo `gemini` sai da empresa sem análise. Isso é aceitável apenas em ambiente de POC/desenvolvimento e deve ficar explícito no README. Para não criar um atalho que contorne o futuro Security Router, o LibreChat não recebe nenhuma chave de provedor e não alcança provedores nem a IA Local diretamente — somente o LiteLLM.

## Capabilities

### New Capabilities

- `platform-deployment`: execução 100% Docker Compose da plataforma — serviços, imagens versionadas, redes segmentadas, health checks, exposição mínima de portas e gestão de secrets via `.env`.
- `ai-gateway`: LiteLLM como ponto único e obrigatório de acesso a modelos, API OpenAI-compatible, catálogo de modelos (`local-ai`, `gemini`), streaming, contabilização de tokens e chaves de provedor centralizadas.
- `chat-portal`: LibreChat como portal autenticado com histórico, usando exclusivamente o gateway e propagando identidade do usuário e da conversa.
- `local-ai`: IA Local self-hosted (Ollama) servindo modelo via API OpenAI-compatible apenas na rede interna.

### Modified Capabilities

(nenhuma — não há specs existentes)

## Impact

- **Novos arquivos**: `docker-compose.yml`, `.env.example`, `.gitignore`, `README.md`, `librechat/librechat.yaml`, `litellm/config.yaml`, `local-ai/` (script de pull do modelo), `scripts/`, `tests/smoke/`, `docs/architecture.md`.
- **Dependências externas**: imagens Docker oficiais (registry.librechat.ai, docker.litellm.ai, docker.langfuse.com, mcr.microsoft.com, Docker Hub); download do modelo Ollama (~2 GB); chave da API Gemini fornecida pelo usuário.
- **Host**: requer Docker 29 + Compose v2; para usar a GPU, também driver NVIDIA (já instalado: 595.91.07) e `nvidia-container-toolkit` (1.20.1, já instalado). Consumo estimado de memória da stack completa: 8–12 GB. A porta 5432 já está ocupada no host por outro projeto — nenhum banco desta stack será publicado no host.
- **Código existente**: nenhum (projeto greenfield).
