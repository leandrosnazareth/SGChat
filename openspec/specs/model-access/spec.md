# model-access Specification

## Purpose
Controla quais modelos de IA cada usuário pode usar, com base em grupos definidos numa política corporativa, garantindo que a autorização aconteça no gateway e não possa ser contornada pela interface nem por chamadas diretas.

## Requirements

### Requirement: Política de acesso configurável
O acesso a modelos SHALL ser definido por uma política declarativa, fora do código, que associa grupos a modelos permitidos e usuários (por e-mail) a um ou mais grupos. Alterar permissões MUST NOT exigir mudança de código.

#### Scenario: Usuário em mais de um grupo
- **WHEN** um usuário pertence a dois grupos com listas de modelos diferentes
- **THEN** ele pode usar a união dos modelos permitidos pelos dois grupos

#### Scenario: Usuário não listado
- **WHEN** um usuário autenticado não aparece na política
- **THEN** recebe as permissões do grupo padrão definido na política

### Requirement: Autorização no gateway antes da chamada ao modelo
O gateway SHALL verificar a permissão do usuário para o modelo solicitado antes de encaminhar a requisição a qualquer modelo, local ou externo.

#### Scenario: Modelo permitido
- **WHEN** um usuário solicita um modelo permitido para algum de seus grupos
- **THEN** a requisição é encaminhada normalmente ao modelo

#### Scenario: Modelo não permitido
- **WHEN** um usuário solicita um modelo que nenhum de seus grupos permite
- **THEN** o gateway responde HTTP 403 com erro no formato OpenAI cujo `message` é `MODEL_ACCESS_DENIED`, e nada é encaminhado ao modelo

#### Scenario: Chamada direta ao gateway
- **WHEN** uma requisição chega diretamente ao gateway, sem passar pela interface do portal, em nome de um usuário sem permissão para o modelo
- **THEN** a resposta é a mesma HTTP 403 `MODEL_ACCESS_DENIED`

### Requirement: Identidade obrigatória para requisições do portal
Toda requisição feita com a credencial do portal SHALL identificar o usuário final pelo e-mail, informado como identificador de usuário final do gateway. Sem identidade, a requisição MUST ser negada.

#### Scenario: Requisição do portal sem identidade
- **WHEN** uma requisição com a credencial do portal não informa o e-mail do usuário
- **THEN** o gateway responde HTTP 403 `MODEL_ACCESS_DENIED` e nada é encaminhado ao modelo

#### Scenario: Mesma identidade para chamada e listagem
- **WHEN** o portal lista modelos e depois envia uma mensagem em nome do mesmo usuário
- **THEN** as duas operações são avaliadas com o mesmo identificador de usuário final

### Requirement: Comportamento fail-closed
Na impossibilidade de avaliar a política com segurança, o gateway SHALL negar o acesso em vez de permiti-lo.

#### Scenario: Política ausente ou inválida
- **WHEN** o arquivo de política não existe ou não pode ser interpretado
- **THEN** todas as requisições de usuários são negadas com HTTP 403 `MODEL_ACCESS_DENIED` e o erro é registrado no log do gateway

#### Scenario: Modelo fora da política
- **WHEN** um usuário solicita um modelo que não aparece em nenhum grupo da política
- **THEN** a requisição é negada com HTTP 403 `MODEL_ACCESS_DENIED`

### Requirement: Recarga da política sem reinício
Alterações no arquivo de política SHALL passar a valer nas requisições seguintes sem reiniciar o gateway.

#### Scenario: Permissão removida
- **WHEN** um modelo é removido do grupo de um usuário no arquivo de política
- **THEN** a próxima requisição desse usuário para esse modelo é negada, sem reinício do gateway

### Requirement: Registro das negações
Cada requisição negada SHALL ficar registrada com o usuário, o modelo solicitado e o motivo da negação.

#### Scenario: Negação registrada
- **WHEN** uma requisição é negada por falta de permissão
- **THEN** o log do gateway contém o usuário, o modelo solicitado e o motivo, sem expor credenciais

### Requirement: Acesso administrativo
Requisições autenticadas com a chave mestra do gateway SHALL ser tratadas como administrativas e não estão sujeitas à política de usuários.

#### Scenario: Chamada com a chave mestra
- **WHEN** um administrador chama o gateway com a chave mestra
- **THEN** a requisição é atendida para qualquer modelo do catálogo, independentemente da política de usuários

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
