# usage-quotas Specification

## Purpose
Limita o consumo de modelos de IA por tokens e por custo, em períodos configuráveis, redirecionando para a IA Local ou bloqueando quando um limite é atingido, e registra o uso e o custo para controle financeiro.

## Requirements

### Requirement: Política de cotas configurável
Cotas SHALL ser definidas fora do código, por regras que combinam alvo (um usuário, cada membro de um grupo, cada usuário, ou o total global), modelos (lista ou "todos os externos"), período (diário, semanal ou mensal) e limite de tokens e/ou de custo em US$. Alterações MUST valer sem reiniciar o gateway.

#### Scenario: Regra por usuário e modelo
- **WHEN** existe uma regra de 300.000 tokens mensais de `gemini` para um usuário
- **THEN** o consumo desse usuário em `gemini` no mês corrente é comparado a esse limite

#### Scenario: Várias regras aplicáveis
- **WHEN** mais de uma regra se aplica à mesma requisição
- **THEN** a requisição é tratada como excedida se qualquer uma delas estiver esgotada

#### Scenario: Alteração sem reinício
- **WHEN** o limite de uma regra é alterado no arquivo
- **THEN** a próxima requisição já é avaliada com o novo limite

### Requirement: IA Local sem cota
Modelos declarados como ilimitados (a IA Local) MUST NOT ser limitados por cotas de tokens ou de custo.

#### Scenario: Usuário com cota externa esgotada usa a IA Local
- **WHEN** um usuário com a cota externa esgotada solicita `local-ai`
- **THEN** a requisição é atendida normalmente

### Requirement: Contabilização do consumo
O consumo SHALL ser medido em `prompt_tokens`, `completion_tokens`, `total_tokens` e custo por requisição bem-sucedida, por usuário e modelo, e considerado imediatamente nas requisições seguintes, mesmo antes de gravado no banco.

#### Scenario: Consumo recente considerado
- **WHEN** um usuário faz uma requisição que leva o consumo acima do limite e envia outra logo em seguida
- **THEN** a segunda requisição já é tratada como excedida

### Requirement: Redirecionamento para a IA Local ao exceder
Com a ação `LOCAL_FALLBACK`, uma requisição a modelo cuja cota ou budget estejam esgotados SHALL ser atendida pela IA Local, desde que a política de acesso permita a IA Local ao usuário.

#### Scenario: Cota de tokens excedida
- **WHEN** um usuário com a cota de tokens de `gemini` esgotada solicita `gemini`
- **THEN** a resposta é gerada pela IA Local e o registro de uso grava `requested_model=gemini`, `effective_model=local-ai` e `routing_reason=TOKEN_QUOTA_EXCEEDED`

#### Scenario: Budget excedido
- **WHEN** um usuário com o budget em US$ esgotado solicita um modelo externo
- **THEN** a resposta é gerada pela IA Local com `routing_reason=BUDGET_EXCEEDED`

#### Scenario: IA Local não permitida ao usuário
- **WHEN** o redirecionamento seria necessário mas a política de acesso não permite a IA Local ao usuário
- **THEN** a requisição é bloqueada em vez de redirecionada

### Requirement: Bloqueio ao exceder
Com a ação `BLOCK`, uma requisição a modelo cuja cota ou budget estejam esgotados SHALL ser recusada com HTTP 429, sem chamada ao modelo, informando `TOKEN_QUOTA_EXCEEDED` ou `BUDGET_EXCEEDED`.

#### Scenario: Bloqueio por cota
- **WHEN** a ação configurada é `BLOCK` e a cota do usuário está esgotada
- **THEN** o gateway responde HTTP 429 com `TOKEN_QUOTA_EXCEEDED` e nada é encaminhado ao modelo

### Requirement: Fail-closed na avaliação de cotas
Se o consumo não puder ser apurado ou a política de cotas for inválida, requisições a modelos com cota SHALL receber a ação configurada, como se o limite estivesse esgotado.

#### Scenario: Banco de uso indisponível
- **WHEN** a consulta ao consumo falha
- **THEN** a requisição a modelo externo é redirecionada à IA Local ou bloqueada, conforme a ação, e o erro é registrado no log do gateway

#### Scenario: Política de cotas inválida
- **WHEN** o arquivo de cotas não pode ser interpretado
- **THEN** requisições a modelos externos recebem a ação configurada e requisições à IA Local seguem normalmente

### Requirement: Relatório de custos
O sistema SHALL permitir consultar consumo de tokens e custo agregados por usuário, grupo, modelo, provedor e período.

#### Scenario: Custo por grupo no mês
- **WHEN** o administrador pede o relatório do mês corrente agrupado por grupo
- **THEN** o resultado lista, por grupo, total de requisições, tokens e custo em US$, considerando o grupo do usuário no momento de cada requisição
