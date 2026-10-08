# Spec Delta

## Purpose

Garante que conteúdo sensível não saia da infraestrutura corporativa: classifica todo o contexto enviado aos modelos e decide, antes de qualquer chamada externa, se ele pode seguir ao modelo externo, deve ser atendido pela IA Local ou deve ser bloqueado.

## ADDED Requirements

### Requirement: Classificação em quatro níveis
Toda requisição a modelo SHALL receber uma classificação entre `PUBLIC`, `INTERNAL`, `CONFIDENTIAL` e `RESTRICTED`, igual à evidência mais restritiva encontrada pelos detectores.

#### Scenario: Conteúdo genérico
- **WHEN** o usuário pergunta "Explique Virtual Threads em Java."
- **THEN** a classificação é `PUBLIC` e o envio externo é permitido

#### Scenario: Evidências conflitantes
- **WHEN** um detector indica `RESTRICTED` e outros indicam `PUBLIC`
- **THEN** a classificação final é `RESTRICTED`

### Requirement: Análise do contexto completo
A classificação SHALL considerar todo o conteúdo que seria enviado ao modelo: mensagem atual, histórico, instruções de sistema, partes de texto, argumentos e resultados de ferramentas.

#### Scenario: Dado sensível no histórico
- **WHEN** uma mensagem anterior da conversa contém conteúdo confidencial e a mensagem atual é genérica
- **THEN** a requisição é classificada como `CONFIDENTIAL`

### Requirement: Detectores configuráveis
Os detectores (expressões regulares, palavras-chave, clientes e projetos classificados, entidades do Presidio) e a classificação de cada um SHALL ser definidos em configuração, sem regras empresariais no código, e alterações MUST valer sem reiniciar o gateway.

#### Scenario: Novo cliente confidencial
- **WHEN** o administrador inclui um cliente na lista de entidades confidenciais
- **THEN** a próxima mensagem que menciona esse cliente é classificada como `CONFIDENTIAL`

#### Scenario: Dados pessoais brasileiros
- **WHEN** a mensagem contém um CPF ou CNPJ com dígito verificador válido
- **THEN** a classificação é ao menos `CONFIDENTIAL`

#### Scenario: Número que não é documento
- **WHEN** a mensagem contém uma sequência no formato de CPF com dígito verificador inválido
- **THEN** essa sequência não é tratada como CPF

### Requirement: Conteúdo confidencial não sai da empresa
Requisições `CONFIDENTIAL` a um modelo externo SHALL ser atendidas pela IA Local, mesmo que o usuário tenha escolhido o modelo externo; o mesmo vale para `INTERNAL` com a política padrão.

#### Scenario: Usuário escolhe o modelo externo para conteúdo confidencial
- **WHEN** o usuário envia "Analise o contrato confidencial do cliente XPTO." escolhendo `gemini`
- **THEN** a resposta é gerada pela IA Local e o registro grava `requested_model=gemini`, `effective_model=local-ai`, `classification=CONFIDENTIAL` e `routing_reason=SECURITY_POLICY`

#### Scenario: IA Local não permitida ao usuário
- **WHEN** o conteúdo precisa ir para a IA Local mas a política de acesso não a permite ao usuário
- **THEN** a requisição é bloqueada e nada é enviado ao modelo externo

### Requirement: Conteúdo restrito é bloqueado
Requisições `RESTRICTED` (senhas, chaves de API, tokens, credenciais, informação explicitamente restrita) MUST NOT ser enviadas a modelo externo e, pela política padrão, SHALL ser recusadas com HTTP 403 `SECURITY_POLICY_BLOCKED`, inclusive quando pedidas à IA Local.

#### Scenario: Chave de API na mensagem
- **WHEN** o usuário envia "Minha API Key é sk-xxxxxxxx."
- **THEN** a classificação é `RESTRICTED`, o envio externo não é permitido e o gateway responde HTTP 403 `SECURITY_POLICY_BLOCKED`

### Requirement: Fail-closed na análise
Se a análise não puder ser concluída (Presidio indisponível, política inválida ou erro interno), o conteúdo MUST NOT ser enviado a modelo externo e SHALL ser tratado como `CONFIDENTIAL`.

#### Scenario: Presidio indisponível
- **WHEN** o detector de PII não responde e o usuário pede o modelo externo
- **THEN** a requisição é atendida pela IA Local e o registro indica a indisponibilidade do detector

### Requirement: IA Local indisponível não libera a nuvem
Quando a requisição foi direcionada à IA Local por segurança e a IA Local está indisponível, o gateway SHALL retornar erro e MUST NOT enviar o conteúdo ao modelo externo.

#### Scenario: IA Local desligada
- **WHEN** a IA Local está fora do ar e o usuário envia conteúdo confidencial escolhendo `gemini`
- **THEN** o usuário recebe erro e nenhuma chamada é feita ao modelo externo

### Requirement: Registro sem exposição do conteúdo
Cada decisão SHALL ser registrada com classificação, permissão de envio externo, motivos (nomes das regras e tipos de entidade), modelo pedido e modelo efetivo, e MUST NOT incluir o trecho sensível encontrado.

#### Scenario: Registro de um bloqueio
- **WHEN** uma mensagem com senha é bloqueada
- **THEN** o log e o registro de uso contêm a classificação e o nome da regra, mas não a senha
