# chat-portal Specification

## Purpose
Define o portal de chat corporativo: autenticação, histórico de conversas e seleção de modelos, consumindo IA exclusivamente por meio do AI Gateway.

## Requirements

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
- **THEN** apenas o endpoint do gateway aparece, com somente os modelos que a política de acesso permite a esse usuário

#### Scenario: Usuários com permissões diferentes
- **WHEN** um usuário do grupo FINANCE e um do grupo DEVELOPER abrem o seletor
- **THEN** o de FINANCE vê `auto` e `local-ai`, e o de DEVELOPER vê `auto`, `local-ai` e `gemini`

#### Scenario: Falha na busca de modelos
- **WHEN** o portal não consegue obter a lista de modelos do gateway
- **THEN** o seletor mostra apenas `local-ai`, nunca um modelo externo

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
Em cada chamada ao gateway (mensagens e listagem de modelos), o portal SHALL enviar o e-mail do usuário autenticado como identificador de usuário final e, nas mensagens, o identificador da conversa. O e-mail é usado pelo gateway para aplicar a política de acesso a modelos e para registrar o uso.

#### Scenario: Identificadores enviados
- **WHEN** o usuário envia uma mensagem
- **THEN** a requisição ao gateway contém o e-mail do usuário como identificador de usuário final e o ID da conversa, e o registro de uso do gateway mostra esse e-mail

#### Scenario: Modelo negado pelo gateway
- **WHEN** o usuário envia uma mensagem para um modelo que sua política não permite
- **THEN** o portal exibe um erro e nenhuma resposta de modelo é gerada
