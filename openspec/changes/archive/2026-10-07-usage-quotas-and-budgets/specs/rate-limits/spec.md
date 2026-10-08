# Spec Delta

## Purpose

Protege o gateway e os modelos contra uso excessivo em curtos intervalos, limitando requisições e tokens por minuto de cada usuário final.

## ADDED Requirements

### Requirement: Limite de requisições por minuto por usuário
O gateway SHALL limitar o número de requisições por minuto de cada usuário final a um valor configurável (padrão 30). Requisições acima do limite MUST ser recusadas com HTTP 429.

#### Scenario: Usuário excede o RPM
- **WHEN** um usuário envia mais requisições em um minuto do que o limite configurado
- **THEN** as requisições excedentes recebem HTTP 429 e não são encaminhadas a nenhum modelo

#### Scenario: Limites independentes por usuário
- **WHEN** um usuário atinge o limite
- **THEN** outros usuários continuam sendo atendidos normalmente

### Requirement: Limite de tokens por minuto por usuário
O gateway SHALL limitar os tokens processados por minuto de cada usuário final a um valor configurável (padrão 50.000).

#### Scenario: Usuário excede o TPM
- **WHEN** o consumo de tokens de um usuário no minuto corrente ultrapassa o limite
- **THEN** novas requisições desse usuário recebem HTTP 429 até a janela seguinte

### Requirement: Configuração sem código
Os limites de RPM e TPM SHALL ser configurados por variáveis de ambiente, sem alteração de código.

#### Scenario: Alteração de limite
- **WHEN** o administrador altera `RATE_LIMIT_RPM` e reaplica a configuração do gateway
- **THEN** o novo limite passa a valer para todos os usuários finais
