# Spec Delta

## Purpose

Define o portal de chat corporativo: autenticação, histórico de conversas e seleção de modelos, consumindo IA exclusivamente por meio do AI Gateway.

## ADDED Requirements

### Requirement: Autenticação obrigatória
O portal SHALL exigir login para qualquer uso do chat. Usuários não autenticados MUST NOT conseguir enviar mensagens.

#### Scenario: Acesso sem login
- **WHEN** um visitante não autenticado acessa o portal
- **THEN** é direcionado à tela de login e não consegue enviar mensagens

#### Scenario: Login válido
- **WHEN** um usuário registrado informa credenciais válidas
- **THEN** acessa o chat com seu histórico de conversas

### Requirement: Gateway como único endpoint de modelos
O portal SHALL usar o AI Gateway como único endpoint de modelos. Endpoints nativos de provedores (OpenAI, Google, Anthropic etc.) MUST estar desabilitados, e o portal MUST NOT possuir chaves de provedores.

#### Scenario: Endpoints disponíveis
- **WHEN** um usuário autenticado abre o seletor de modelos
- **THEN** apenas o endpoint do gateway aparece, com os modelos `local-ai` e `gemini`

#### Scenario: Gateway indisponível
- **WHEN** o gateway está fora do ar e o usuário envia uma mensagem
- **THEN** o portal exibe erro ao usuário e não tenta nenhum caminho alternativo até um modelo

### Requirement: Conversa com resposta em streaming
O portal SHALL exibir as respostas do modelo de forma incremental (streaming).

#### Scenario: Mensagem enviada
- **WHEN** o usuário envia uma mensagem selecionando `local-ai` ou `gemini`
- **THEN** a resposta aparece progressivamente na conversa

### Requirement: Histórico de conversas
O portal SHALL persistir as conversas de cada usuário e permitir retomá-las com o contexto anterior.

#### Scenario: Continuação de conversa
- **WHEN** o usuário reabre uma conversa anterior e envia nova mensagem
- **THEN** a conversa exibe as mensagens anteriores e o modelo recebe o histórico como contexto

#### Scenario: Isolamento entre usuários
- **WHEN** um usuário lista suas conversas
- **THEN** não vê conversas de outros usuários

### Requirement: Propagação de identidade
Em cada chamada ao gateway, o portal SHALL enviar o identificador do usuário autenticado e o identificador da conversa.

#### Scenario: Identificadores enviados
- **WHEN** o usuário envia uma mensagem
- **THEN** a requisição ao gateway contém o ID do usuário e o ID da conversa, correspondentes aos registrados no portal
