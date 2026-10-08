# Spec Delta

## Purpose

Estabelece o AI Gateway como ponto único e obrigatório de acesso a modelos de IA, expondo API OpenAI-compatible, centralizando as credenciais de provedores e medindo o consumo de tokens.

## ADDED Requirements

### Requirement: API OpenAI-compatible
O gateway SHALL expor os endpoints `GET /v1/models` e `POST /v1/chat/completions` compatíveis com a API OpenAI, autenticados por chave de acesso do gateway.

#### Scenario: Listagem de modelos
- **WHEN** um cliente autenticado chama `GET /v1/models`
- **THEN** a resposta lista os modelos configurados no gateway, incluindo `local-ai` e `gemini`

#### Scenario: Requisição sem chave válida
- **WHEN** um cliente chama qualquer endpoint de modelo sem chave ou com chave inválida
- **THEN** o gateway responde HTTP 401 e não encaminha nada a nenhum modelo

### Requirement: Ponto único de acesso a modelos
Todo acesso a modelos (locais ou externos) SHALL passar pelo gateway. Nenhum outro serviço da plataforma MUST possuir credenciais de provedores externos ou rota de rede direta para a IA Local.

#### Scenario: Credenciais centralizadas
- **WHEN** as variáveis de ambiente de todos os containers são inspecionadas
- **THEN** `GEMINI_API_KEY` (e demais chaves de provedor) existe somente no container do gateway

### Requirement: Catálogo de modelos com nomes corporativos
O gateway SHALL expor modelos por nomes lógicos da plataforma (`local-ai`, `gemini`), desacoplados do nome do modelo no provedor, que MUST ser configurável sem alteração de código.

#### Scenario: Troca do modelo subjacente
- **WHEN** o modelo do provedor associado a `gemini` ou `local-ai` é alterado na configuração
- **THEN** clientes continuam usando o mesmo nome lógico sem nenhuma mudança

### Requirement: Encaminhamento para IA Local
Requisições ao modelo `local-ai` SHALL ser atendidas pela IA Local na rede interna, sem tráfego para fora da infraestrutura.

#### Scenario: Chat com IA Local
- **WHEN** um cliente envia `POST /v1/chat/completions` com `model: local-ai`
- **THEN** a resposta é gerada pela IA Local e retorna no formato OpenAI

### Requirement: Encaminhamento para provedor externo
Requisições ao modelo `gemini` SHALL ser encaminhadas ao Google Gemini usando a chave configurada no gateway.

#### Scenario: Chat com Gemini
- **WHEN** um cliente envia `POST /v1/chat/completions` com `model: gemini` e a chave do provedor é válida
- **THEN** a resposta é gerada pelo Gemini e retorna no formato OpenAI

#### Scenario: Chave do provedor ausente ou inválida
- **WHEN** a chave do Gemini não está configurada ou é rejeitada pelo provedor
- **THEN** o gateway retorna erro no formato OpenAI com status HTTP de erro, sem redirecionar silenciosamente a requisição para outro modelo

### Requirement: Streaming
O gateway SHALL suportar respostas em streaming (`stream: true`, Server-Sent Events) para todos os modelos do catálogo.

#### Scenario: Resposta em streaming
- **WHEN** um cliente envia requisição com `stream: true`
- **THEN** a resposta chega incrementalmente em eventos SSE e termina com `data: [DONE]`

### Requirement: Contabilização de tokens
Cada resposta SHALL informar o uso de tokens (`prompt_tokens`, `completion_tokens`, `total_tokens`), e o gateway SHALL persistir o uso por requisição com o modelo utilizado.

#### Scenario: Uso informado na resposta
- **WHEN** uma requisição não-streaming é concluída
- **THEN** o campo `usage` da resposta contém `prompt_tokens`, `completion_tokens` e `total_tokens` maiores que zero

#### Scenario: Uso persistido
- **WHEN** uma requisição a qualquer modelo é concluída
- **THEN** o registro de uso do gateway contém o modelo, a contagem de tokens e o custo (zero para `local-ai`)

### Requirement: Identidade do chamador
O gateway SHALL aceitar e registrar junto a cada requisição o identificador do usuário final e da conversa enviados pelo portal.

#### Scenario: Identidade registrada
- **WHEN** o portal envia uma requisição com identificador de usuário e de conversa
- **THEN** o registro de uso do gateway associa a requisição a esse usuário e a essa conversa
