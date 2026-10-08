# Corporate AI Platform (SGChat)

Plataforma corporativa self-hosted para uso seguro, governado e auditável de IA: um portal de chat
(LibreChat) que acessa modelos locais e externos exclusivamente por um AI Gateway (LiteLLM), com
observabilidade (Langfuse), detecção de PII (Presidio) e IA Local (Ollama). Tudo roda em Docker.

## Fluxograma geral resumido

![Fluxograma geral do SGChat: segurança, roteamento de IA e auditoria](fluxograma.png)

## Pré-requisitos

- Docker 29+ com Docker Compose v2 — nada mais é instalado no host.
- ~12 GB de RAM livres para a stack completa e ~15 GB de disco (imagens + modelo).
- `openssl` e `python3` no host (usados pelos scripts auxiliares).
- Opcional: GPU NVIDIA + `nvidia-container-toolkit` para acelerar a IA Local
  ([instalação](docs/architecture.md#instalação-do-nvidia-container-toolkit-ubuntu-requer-sudo)).
- Opcional: chave da API Gemini (Google AI Studio) para o modelo `gemini`.

## Subir a plataforma

```bash
git clone <url-do-repositório> && cd SGChat
scripts/init-env.sh          # cria o .env a partir do .env.example e gera todos os secrets
```

Edite o `.env`:

- `GEMINI_API_KEY=` — sua chave do Gemini (opcional; sem ela o modelo `gemini` retorna erro).
- `COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml` — somente se o host tiver GPU NVIDIA
  com `nvidia-container-toolkit` (padrão: CPU).

```bash
docker compose up -d
scripts/healthcheck.sh       # aguarde até todos os serviços aparecerem como healthy
```

A **primeira subida demora** (vários minutos): download de ≈8 GB de imagens e do modelo
`qwen2.5:3b` (≈2 GB). As seguintes levam menos de 1 minuto.

## Criar usuários e acessar

O auto-registro está desabilitado; usuários são criados pelo administrador:

```bash
scripts/create-user.sh maria@empresa.local "Maria Silva" maria.silva
```

A senha gerada é exibida no terminal (ou, com `--save` no fim, gravada em `.test-users`, arquivo
local ignorado pelo git). Defina uma senha própria com `PASSWORD=... scripts/create-user.sh ...`.

| Interface | Endereço | Acesso |
|---|---|---|
| Portal de chat (LibreChat) | http://localhost:3080 | usuários criados acima |
| Langfuse (observabilidade) | http://localhost:3000 | `LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD` do `.env` |
| Portal de auditoria (Fase 9) | http://localhost:3090 | perfis AUDITOR / MASTER_AUDITOR ([docs/audit.md](docs/audit.md)) |
| AI Gateway (LiteLLM, só loopback) | http://127.0.0.1:4000 | `LITELLM_MASTER_KEY` do `.env` |

Modelos disponíveis no portal: **`auto`** (o gateway escolhe), **`local-ai`** (IA Local, dados não
saem da empresa) e **`gemini`** (Google, externo).

## Tour visual

Telas capturadas da aplicação em execução (stack local, usuários de teste), sem montagens.
Para gerá-las de novo: `scripts/capture-screenshots.sh` (Playwright em container; cria conversas reais).

### 1. Portal de chat

| | |
|---|---|
| ![Tela de login do portal de chat](assets/screenshots/01-chat-login.png) | ![Seletor de modelos com auto, local-ai e gemini](assets/screenshots/02-chat-seletor-modelos.png) |
| **Login.** Cada pessoa entra com a conta criada pelo administrador (`scripts/create-user.sh`); não há auto-registro. | **Seletor de modelos.** Só existe o endpoint *Corporate AI* (o gateway). Ana, do grupo `DEVELOPER`, vê `local-ai`, `auto` e `gemini`. |

### 2. Roteamento e segurança em ação

| | |
|---|---|
| ![Pergunta simples respondida com o modelo auto](assets/screenshots/03-chat-auto-pergunta-simples.png) | ![Pergunta pública respondida pelo Gemini](assets/screenshots/04-chat-gemini-pergunta-publica.png) |
| **`auto` com pergunta simples.** "Quanto é 15% de 200?" é classificada como simples, e o gateway escolhe a IA Local (custo zero). | **Pergunta pública ao `gemini`.** Conteúdo `PUBLIC` pode sair: a resposta vem do Google. |
| ![Pedido confidencial atendido pela IA Local](assets/screenshots/05-chat-confidencial-ia-local.png) | ![Mensagem com senha bloqueada](assets/screenshots/06-chat-segredo-bloqueado.png) |
| **Conteúdo confidencial com `gemini` selecionado.** O Security Router reconhece o cliente classificado *XPTO* e a palavra "confidencial", e a resposta é gerada pela **IA Local**. Nada sai da empresa. | **Segredo.** Uma senha na mensagem é `RESTRICTED`: a requisição é bloqueada (`403 SECURITY_POLICY_BLOCKED`) antes de chegar a qualquer modelo. |

![Seletor de modelos de um usuário do grupo FINANCE](assets/screenshots/07-chat-seletor-finance.png)

**Controle de acesso por grupo.** Bruno (`FINANCE`) não vê o `gemini`: o seletor mostra só `local-ai` e `auto`, e o `auto` dele nunca escolhe o modelo externo.

### 3. Auditoria no Langfuse

![Lista de traces no Langfuse](assets/screenshots/08-langfuse-traces.png)

**Traces.** Toda chamada ao gateway, atendida ou bloqueada, vira um trace com usuário, conversa, entrada, saída, tokens, custo e latência.

| | |
|---|---|
| ![Trace de um pedido confidencial redirecionado](assets/screenshots/09-langfuse-trace-redirecionado.png) | ![Trace de um bloqueio com o segredo redigido](assets/screenshots/09b-langfuse-segredo-redigido.png) |
| **Decisão explicada.** O pedido confidencial mostra `requested_model=gemini`, `effective_model=local-ai`, `routing_reason=SECURITY_POLICY`, `classification=CONFIDENTIAL` e as regras que dispararam, também como tags. | **Segredos não são gravados.** No bloqueio, a senha é substituída por `[REDACTED: RESTRICTED — regex:credencial-declarada]` antes do registro (`content_redacted=true`). |

![Filtro por tag no Langfuse](assets/screenshots/10-langfuse-filtro-por-tag.png)

**Busca por decisão.** Filtro por tag `routing:SECURITY_POLICY`: só as chamadas que a segurança redirecionou ou bloqueou. Também há filtro por metadado (`classification`, `requested_model`, `effective_model`, `routing_reason`, `blocked`).

### 4. Portal de auditoria (quem audita também é auditado)

| | |
|---|---|
| ![Login do portal de auditoria](assets/screenshots/11-auditoria-login.png) | ![Motivo obrigatório para abrir uma conversa](assets/screenshots/13-auditoria-motivo.png) |
| **Login** com a conta do portal de chat. Só os perfis `AUDITOR` e `MASTER_AUDITOR` entram, e o aviso deixa claro que todo acesso é registrado. | **Motivo obrigatório.** Para abrir ou exportar uma conversa, o `MASTER_AUDITOR` informa o motivo, que vai para a trilha. |

![Busca do auditor master com todas as chamadas de um usuário](assets/screenshots/12-auditoria-busca-master.png)

**Busca (`MASTER_AUDITOR`).** Todas as chamadas da Ana: pedido → modelo utilizado, classificação, motivo do roteamento, regras, tokens, custo e status. Os bloqueios aparecem destacados. Daqui se abre cada conversa.

![Conversa reconstruída com pergunta, resposta e decisão](assets/screenshots/14-auditoria-conversa.png)

**Conversa reconstruída.** Pergunta e resposta em ordem cronológica, com a decisão de cada chamada (inclusive a geração de título do portal), o evento da trilha (#82) e o motivo. A exportação em JSON gera o evento `EXPORT_CONVERSATION`.

![Trilha de acesso imutável dos auditores](assets/screenshots/15-auditoria-trilha.png)

**Trilha de acesso.** Cada login, busca e visualização do auditor, com filtros, motivo, quantidade e IP. A cadeia de hashes é verificada a cada consulta ("Cadeia íntegra"); o banco recusa `UPDATE` e `DELETE`.

| | |
|---|---|
| ![Auditor vê apenas metadados](assets/screenshots/16-auditoria-auditor-so-metadados.png) | ![Auditor sem permissão para abrir conversa](assets/screenshots/17-auditoria-auditor-negado.png) |
| **`AUDITOR`: só metadados.** Carla vê quem, quando, modelos, classificação, regras, tokens e custo, sem link para o conteúdo e sem busca por termo. | **Menor privilégio.** Ao tentar abrir a conversa pela URL, Carla é recusada, e a tentativa fica registrada como `DENIED`. |

![Administrador recusado no portal de auditoria](assets/screenshots/18-auditoria-admin-recusado.png)

**ADMIN ≠ AUDITOR.** O administrador da plataforma, com credenciais válidas, não entra no portal de auditoria; a tentativa é registrada (`LOGIN DENIED`).

## Controle de acesso a modelos

O que cada usuário pode usar é definido por grupos em `litellm/policies/model-access.yaml` e aplicado
pelo gateway (HTTP 403 `MODEL_ACCESS_DENIED` para modelos não permitidos). Usuários não listados caem
no grupo `USER`. Edite o arquivo e a mudança vale na próxima requisição, sem reiniciar.

## Segurança de conteúdo

Toda mensagem é classificada (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED`) pelo Security Router
com regras em `litellm/policies/security.yaml` (CPF/CNPJ, segredos, palavras-chave, clientes e projetos
classificados, domínios internos) e o Presidio. Conteúdo não público escolhendo `gemini` é respondido
pela IA Local; senhas e chaves são bloqueadas (403 `SECURITY_POLICY_BLOCKED`).

## Roteamento automático (`auto`)

Com `auto`, depois da segurança, o gateway escolhe entre os modelos que o usuário pode usar:
perguntas simples vão para a IA Local; pedidos complexos (longos, com código ou de análise) vão para o
modelo externo, se houver permissão, cota e disponibilidade. Regras em `litellm/policies/routing.yaml`,
sem reiniciar.

## Auditoria (Langfuse)

Cada chamada ao gateway, atendida ou bloqueada, vira um trace no Langfuse (http://localhost:3000):
usuário, conversa, pergunta, resposta, modelo pedido e utilizado, motivo, classificação, tokens,
custo e latência. Segredos bloqueados não são gravados. Busca na interface (filtros por tag e
metadado) ou por `scripts/audit-search.sh --user … --classification … --reason … --show`.

Auditores usam o **portal de auditoria** (http://localhost:3090), não o Langfuse: `AUDITOR` vê só
metadados; `MASTER_AUDITOR` abre e exporta conversas informando o motivo; todo acesso vai para uma
trilha imutável (`scripts/audit-access-verify.sh` verifica a cadeia de hashes). `ADMIN` não tem acesso.

## Cotas, budgets e rate limit

O consumo de modelos externos é limitado por regras em `litellm/policies/quotas.yaml` (tokens e US$
por dia, semana ou mês; por usuário, grupo ou empresa). Ao exceder, a requisição é atendida pela IA
Local (`LOCAL_FALLBACK`, padrão) ou bloqueada (`BLOCK`). Cada usuário também tem rate limit
(`RATE_LIMIT_RPM`=30, `RATE_LIMIT_TPM`=50.000 no `.env`). Consumo e custo:
`scripts/usage-report.sh --by user|group|model|provider`.
