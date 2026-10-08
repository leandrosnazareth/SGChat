# Spec Delta

## MODIFIED Requirements

### Requirement: Propagação de identidade
Em cada chamada ao gateway, o portal SHALL enviar o identificador do usuário autenticado, o e-mail desse usuário e o identificador da conversa. O e-mail é usado pelo gateway para aplicar a política de acesso a modelos.

#### Scenario: Identificadores enviados
- **WHEN** o usuário envia uma mensagem
- **THEN** a requisição ao gateway contém o ID do usuário, o e-mail do usuário e o ID da conversa, correspondentes aos registrados no portal

#### Scenario: Modelo negado pelo gateway
- **WHEN** o usuário envia uma mensagem para um modelo que sua política não permite
- **THEN** o portal exibe um erro e nenhuma resposta de modelo é gerada
