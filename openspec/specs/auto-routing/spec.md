# auto-routing Specification

## Purpose
Permite ao usuário escolher o modelo `auto` e deixar o gateway decidir qual modelo atende cada requisição, respeitando segurança, permissão, cota e budget, e equilibrando complexidade, disponibilidade, custo e qualidade.

## Requirements

### Requirement: Modelo AUTO
O gateway SHALL oferecer o modelo lógico `auto`; uma requisição a `auto` SHALL ser atendida por um modelo concreto do catálogo, escolhido pelo gateway.

#### Scenario: Requisição ao AUTO
- **WHEN** um usuário autorizado envia uma mensagem escolhendo `auto`
- **THEN** a resposta é gerada por um modelo concreto, e o registro de uso contém `requested_model=auto` e o `effective_model` escolhido

### Requirement: Segurança antes da escolha
O conteúdo de requisições a `auto` SHALL ser avaliado pelas mesmas regras de segurança aplicadas a modelos externos, antes da escolha. Conteúdo que não pode sair MUST ser atendido pela IA Local, qualquer que seja o resultado da escolha automática.

#### Scenario: Conteúdo confidencial no AUTO
- **WHEN** o usuário envia "Analise o contrato confidencial do cliente XPTO." escolhendo `auto`
- **THEN** a resposta é gerada pela IA Local com `routing_reason=SECURITY_POLICY`

### Requirement: Escolha restrita a modelos permitidos e com cota
A escolha automática SHALL considerar apenas modelos que a política de acesso permite ao usuário e cuja cota de tokens e budget não estejam esgotados.

#### Scenario: Usuário sem permissão para o modelo externo
- **WHEN** um usuário do grupo FINANCE envia uma pergunta complexa escolhendo `auto`
- **THEN** a resposta é gerada pela IA Local e o registro indica que o modelo externo foi excluído por permissão

#### Scenario: Cota externa esgotada
- **WHEN** a cota do usuário para `gemini` está esgotada e ele envia uma pergunta complexa escolhendo `auto`
- **THEN** a resposta é gerada pela IA Local e o registro indica que `gemini` foi excluído por cota

### Requirement: Escolha por complexidade, custo e qualidade
A requisição SHALL receber uma faixa de complexidade a partir de sinais determinísticos configuráveis; o gateway SHALL escolher o candidato de menor custo cuja qualidade atenda à faixa e, se nenhum atender, o de maior qualidade disponível.

#### Scenario: Pergunta simples
- **WHEN** o usuário pergunta "Quanto é 15% de 200?" escolhendo `auto`
- **THEN** a faixa é `simple` e o modelo escolhido é a IA Local (custo zero)

#### Scenario: Pedido complexo e público
- **WHEN** o usuário pede uma análise comparativa detalhada de arquiteturas, sem dados da empresa, escolhendo `auto`, e tem permissão e cota para `gemini`
- **THEN** a faixa é `complex` e o modelo escolhido é `gemini`

### Requirement: Disponibilidade
Modelos com falhas recentes acima do limite configurado SHALL ser excluídos temporariamente da escolha automática.

#### Scenario: Modelo externo instável
- **WHEN** `gemini` falhou repetidamente nos últimos minutos e o usuário envia um pedido complexo escolhendo `auto`
- **THEN** a escolha recai sobre outro modelo disponível e o registro indica `gemini` excluído por indisponibilidade

### Requirement: Fallback quando não há candidato
Se nenhum modelo puder ser escolhido, a requisição SHALL ser atendida pela IA Local, se permitida ao usuário; caso contrário, MUST ser negada com HTTP 403.

#### Scenario: Nenhum candidato
- **WHEN** todos os modelos do perfil estão excluídos para o usuário
- **THEN** a requisição vai para a IA Local, ou é negada se nem a IA Local for permitida

### Requirement: Registro da decisão
Cada escolha automática SHALL registrar `requested_model`, `effective_model`, `routing_reason=AUTO` e os detalhes da decisão (faixa, pontuação, sinais, regra usada e modelos excluídos com o motivo), sem o conteúdo da requisição.

#### Scenario: Auditoria de uma escolha
- **WHEN** uma requisição a `auto` é atendida
- **THEN** o registro de uso permite saber por que aquele modelo foi escolhido e por que os demais foram excluídos

### Requirement: Configuração sem reinício
Perfis de modelos (custo, qualidade), faixas, sinais de complexidade e limites de disponibilidade SHALL ser configuráveis em arquivo e as alterações MUST valer sem reiniciar o gateway.

#### Scenario: Ajuste de faixa
- **WHEN** o administrador altera a qualidade mínima da faixa `medium`
- **THEN** a próxima requisição `medium` já é decidida com o novo valor
