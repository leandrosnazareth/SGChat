# Spec Delta

## MODIFIED Requirements

### Requirement: Gateway como único endpoint de modelos
O portal SHALL usar o AI Gateway como único endpoint de modelos. Endpoints nativos de provedores (OpenAI, Google, Anthropic etc.) MUST estar desabilitados, e o portal MUST NOT possuir chaves de provedores.

#### Scenario: Endpoints disponíveis
- **WHEN** um usuário autenticado abre o seletor de modelos
- **THEN** apenas o endpoint do gateway aparece, com somente os modelos que a política de acesso permite a esse usuário

#### Scenario: Usuários com permissões diferentes
- **WHEN** um usuário do grupo FINANCE e um do grupo DEVELOPER abrem o seletor
- **THEN** o de FINANCE vê apenas `local-ai` e o de DEVELOPER vê `local-ai` e `gemini`

#### Scenario: Falha na busca de modelos
- **WHEN** o portal não consegue obter a lista de modelos do gateway
- **THEN** o seletor mostra apenas `local-ai`, nunca um modelo externo

#### Scenario: Gateway indisponível
- **WHEN** o gateway está fora do ar e o usuário envia uma mensagem
- **THEN** o portal exibe erro ao usuário e não tenta nenhum caminho alternativo até um modelo

### Requirement: Propagação de identidade
Em cada chamada ao gateway (mensagens e listagem de modelos), o portal SHALL enviar o e-mail do usuário autenticado como identificador de usuário final e, nas mensagens, o identificador da conversa. O e-mail é usado pelo gateway para aplicar a política de acesso a modelos e para registrar o uso.

#### Scenario: Identificadores enviados
- **WHEN** o usuário envia uma mensagem
- **THEN** a requisição ao gateway contém o e-mail do usuário como identificador de usuário final e o ID da conversa, e o registro de uso do gateway mostra esse e-mail

#### Scenario: Modelo negado pelo gateway
- **WHEN** o usuário envia uma mensagem para um modelo que sua política não permite
- **THEN** o portal exibe um erro e nenhuma resposta de modelo é gerada
