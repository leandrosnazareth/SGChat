# Spec Delta

## Purpose

Registra toda chamada ao AI Gateway no Langfuse self-hosted, com conteúdo, consumo e as decisões corporativas (classificação, roteamento, cota), de forma pesquisável e sem expor segredos.

## ADDED Requirements

### Requirement: Trace para toda chamada
Toda chamada de chat ao gateway SHALL gerar um trace no Langfuse com usuário, conversa, entrada, saída, modelo efetivo, tokens, custo, latência e status, inclusive quando a chamada falha ou é bloqueada.

#### Scenario: Chamada atendida
- **WHEN** um usuário do portal envia uma mensagem que é respondida
- **THEN** o Langfuse tem um trace com o e-mail do usuário, o ID da conversa como sessão, a mensagem, a resposta, o modelo efetivo, os tokens, o custo e a latência

#### Scenario: Chamada bloqueada
- **WHEN** uma requisição é negada pela política de acesso, pela segurança ou por cota em modo `BLOCK`
- **THEN** o Langfuse tem um trace de nível `ERROR` com o motivo, `blocked=true` e sem `effective_model`

### Requirement: Decisões corporativas no trace
O trace SHALL conter, como metadados pesquisáveis, `classification`, `confidence`, `external_allowed`, `requested_model`, `effective_model` e `routing_reason`, além do detalhe de cota (`quota_rule`) e da escolha automática (`auto_decision`) quando houver.

#### Scenario: Redirecionamento por segurança
- **WHEN** o usuário pede `gemini` com conteúdo confidencial
- **THEN** o trace tem `classification=CONFIDENTIAL`, `requested_model=gemini`, `effective_model=local-ai`, `routing_reason=SECURITY_POLICY` e `external_allowed=false`

#### Scenario: Busca
- **WHEN** um auditor busca por usuário, classificação, modelo pedido, modelo efetivo ou motivo do roteamento
- **THEN** o Langfuse retorna exatamente os traces que atendem ao filtro

### Requirement: Segredos não persistidos
Conteúdo classificado como `RESTRICTED` MUST NOT ser gravado no Langfuse; o trace SHALL conter um marcador com as regras que dispararam, sem o valor.

#### Scenario: Senha bloqueada
- **WHEN** o usuário envia "A senha do servidor de produção é Prod@2026!"
- **THEN** a requisição é bloqueada e o trace mostra `[REDACTED: RESTRICTED — regex:credencial-declarada]` em vez da mensagem, em todos os campos

### Requirement: Registro não controlável pelo cliente
O cliente do gateway MUST NOT conseguir desligar, mascarar, renomear, reetiquetar ou redirecionar o registro da própria requisição.

#### Scenario: Tentativa de mascarar o registro
- **WHEN** uma requisição envia `metadata.mask_input=true`, `metadata.tags` ou `metadata.trace_name`
- **THEN** essas chaves são ignoradas, o trace contém a entrada real e as tags do gateway, e o log do gateway registra a tentativa

### Requirement: Disponibilidade independente do Langfuse
Falhas do Langfuse MUST NOT impedir nem atrasar de forma relevante o atendimento das requisições.

#### Scenario: Langfuse fora do ar
- **WHEN** o Langfuse está parado e um usuário envia uma mensagem
- **THEN** a mensagem é respondida normalmente e o gateway registra a falha de envio no log
