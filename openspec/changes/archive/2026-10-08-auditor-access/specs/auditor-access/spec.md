# Spec Delta

## Purpose

Controla quem pode consultar o registro de auditoria das conversas, o quê e registra, de forma imutável, cada acesso de quem audita.

## ADDED Requirements

### Requirement: Perfis de auditoria distintos da administração
Somente usuários dos grupos `AUDITOR` ou `MASTER_AUDITOR` SHALL acessar o portal de auditoria. Administradores da plataforma (`ADMIN`) e demais usuários MUST ser recusados.

#### Scenario: Administrador tenta entrar
- **WHEN** um usuário do grupo `ADMIN` faz login no portal de auditoria com credenciais válidas
- **THEN** o acesso é negado e a tentativa é registrada na trilha com resultado `DENIED`

#### Scenario: Credenciais inválidas
- **WHEN** alguém tenta entrar com senha errada
- **THEN** o acesso é negado e a tentativa é registrada na trilha com resultado `FAILED`

### Requirement: Menor privilégio por perfil
`AUDITOR` SHALL pesquisar e ver apenas metadados das chamadas (usuário, conversa, horário, modelos, classificação, motivo, tokens, custo, status), sem conteúdo. `MASTER_AUDITOR` SHALL também pesquisar por termo, ver e exportar o conteúdo das conversas e consultar a trilha de acesso. As permissões SHALL ser configuráveis por perfil.

#### Scenario: Auditor pesquisa
- **WHEN** um `AUDITOR` pesquisa as chamadas com classificação `CONFIDENTIAL`
- **THEN** recebe os metadados das chamadas, sem pergunta nem resposta

#### Scenario: Auditor tenta abrir conversa
- **WHEN** um `AUDITOR` tenta abrir ou exportar uma conversa
- **THEN** o acesso é negado e a tentativa é registrada com resultado `DENIED`

#### Scenario: Auditor master abre conversa
- **WHEN** um `MASTER_AUDITOR` abre uma conversa informando o motivo
- **THEN** vê as perguntas e respostas em ordem cronológica, com modelo pedido e utilizado, classificação, motivo do roteamento, tokens e custo

### Requirement: Motivo obrigatório para conteúdo
Visualizar ou exportar conteúdo SHALL exigir um motivo informado pelo auditor, gravado na trilha.

#### Scenario: Sem motivo
- **WHEN** um `MASTER_AUDITOR` tenta abrir uma conversa sem informar motivo
- **THEN** o conteúdo não é exibido e o portal pede o motivo

### Requirement: Registro de todo acesso
Cada busca (`SEARCH_CONVERSATIONS`), visualização (`VIEW_CONVERSATION`), exportação (`EXPORT_CONVERSATION`), consulta à trilha (`VIEW_ACCESS_LOG`) e login SHALL gerar um evento `AUDIT_ACCESS` com auditor, perfil, ação, resultado, alvo (usuário e conversa), filtros, motivo, quantidade de resultados, IP de origem e horário. Se o evento não puder ser gravado, o resultado MUST NOT ser entregue.

#### Scenario: Visualização registrada
- **WHEN** um `MASTER_AUDITOR` abre a conversa `conv-123` de `ana` com o motivo "Investigação do chamado 42"
- **THEN** a trilha tem um evento `VIEW_CONVERSATION` com o auditor, `conversation_id=conv-123`, o usuário `ana`, o motivo e o horário

#### Scenario: Trilha indisponível
- **WHEN** o portal não consegue gravar o evento de acesso
- **THEN** o auditor recebe erro e nenhum conteúdo é exibido

### Requirement: Trilha imutável
Eventos da trilha MUST NOT poder ser alterados nem removidos pelo portal, e qualquer adulteração direta no banco SHALL ser detectável por verificação da cadeia de hashes.

#### Scenario: Tentativa de alteração pelo portal
- **WHEN** o usuário de banco do portal tenta `UPDATE` ou `DELETE` em um evento
- **THEN** o banco recusa

#### Scenario: Adulteração pelo superusuário
- **WHEN** um evento é alterado diretamente no banco, contornando as proteções
- **THEN** a verificação da cadeia aponta o primeiro evento inválido

### Requirement: Ferramenta de operação também auditada
Consultas ao registro feitas pela ferramenta de linha de comando de operação SHALL gerar eventos na mesma trilha, identificando o operador e o canal.

#### Scenario: Busca pela linha de comando
- **WHEN** um operador executa `scripts/audit-search.sh --user ana`
- **THEN** a trilha tem um evento `SEARCH_CONVERSATIONS` com `channel=cli`, o operador e os filtros
