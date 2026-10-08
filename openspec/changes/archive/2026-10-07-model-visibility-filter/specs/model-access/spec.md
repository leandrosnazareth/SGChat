# Spec Delta

## ADDED Requirements

### Requirement: Listagem de modelos filtrada pela política
A listagem de modelos do gateway SHALL incluir apenas os modelos permitidos ao usuário que faz a consulta. Um modelo não permitido MUST NOT aparecer na lista, e a consulta individual a ele MUST responder exatamente como para um modelo inexistente, sem revelar que ele existe.

#### Scenario: Usuário com acesso parcial
- **WHEN** um usuário cujo grupo permite apenas `local-ai` lista os modelos
- **THEN** a resposta contém somente `local-ai`

#### Scenario: Consulta individual a modelo oculto
- **WHEN** esse mesmo usuário consulta diretamente o modelo `gemini`
- **THEN** o gateway responde com o mesmo status e erro que daria para um modelo inexistente

#### Scenario: Listagem sem identidade
- **WHEN** uma listagem é feita com credencial que não é a mestra e sem identificar o usuário
- **THEN** a lista retornada é vazia

#### Scenario: Política indisponível na listagem
- **WHEN** a política está ausente ou inválida e um usuário lista os modelos
- **THEN** a lista retornada é vazia

#### Scenario: Listagem administrativa
- **WHEN** a listagem é feita com a chave mestra
- **THEN** todos os modelos do catálogo são retornados

## MODIFIED Requirements

### Requirement: Identidade obrigatória para requisições do portal
Toda requisição feita com a credencial do portal SHALL identificar o usuário final pelo e-mail, informado como identificador de usuário final do gateway. Sem identidade, a requisição MUST ser negada.

#### Scenario: Requisição do portal sem identidade
- **WHEN** uma requisição com a credencial do portal não informa o e-mail do usuário
- **THEN** o gateway responde HTTP 403 `MODEL_ACCESS_DENIED` e nada é encaminhado ao modelo

#### Scenario: Mesma identidade para chamada e listagem
- **WHEN** o portal lista modelos e depois envia uma mensagem em nome do mesmo usuário
- **THEN** as duas operações são avaliadas com o mesmo identificador de usuário final
