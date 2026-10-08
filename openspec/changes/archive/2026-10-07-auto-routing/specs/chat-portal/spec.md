# Spec Delta

## MODIFIED Requirements

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
