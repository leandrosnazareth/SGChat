# Spec Delta

## Purpose

Define como a Corporate AI Platform é implantada e operada inteiramente via Docker Compose, garantindo reprodutibilidade, isolamento de rede e ausência de instalação de aplicações no host.

## ADDED Requirements

### Requirement: Execução 100% em containers
A plataforma SHALL subir integralmente com `docker compose up -d` a partir da raiz do repositório, sem exigir no host nada além de Docker e Docker Compose. Todo serviço de aplicação, banco, cache, armazenamento e IA Local MUST executar em container.

#### Scenario: Subida a partir de um clone limpo
- **WHEN** um desenvolvedor clona o repositório, executa `cp .env.example .env`, preenche os secrets obrigatórios e executa `docker compose up -d`
- **THEN** todos os serviços da plataforma são criados e iniciados sem passos manuais adicionais no host

#### Scenario: Nenhuma dependência instalada no host
- **WHEN** a stack está em execução
- **THEN** nenhum processo de LibreChat, LiteLLM, Langfuse, Presidio, IA Local ou banco de dados roda fora de um container

### Requirement: Imagens versionadas
Cada serviço MUST usar imagem oficial do respectivo projeto com tag de versão explícita; tags móveis (`latest`, `main-stable`, `dev`) MUST NOT ser usadas.

#### Scenario: Inspeção das imagens
- **WHEN** as imagens declaradas no Compose são listadas
- **THEN** cada uma possui uma tag de versão fixa e provém do registro oficial do projeto

### Requirement: Health checks
Cada serviço de longa duração SHALL declarar health check, e serviços dependentes SHALL aguardar suas dependências ficarem saudáveis antes de iniciar.

#### Scenario: Stack saudável
- **WHEN** a stack termina de inicializar
- **THEN** `docker compose ps` reporta todos os serviços com health check como `healthy`

#### Scenario: Dependência indisponível
- **WHEN** um banco do qual um serviço depende não está saudável
- **THEN** o serviço dependente não é iniciado até a dependência ficar saudável

### Requirement: Comunicação por nome de serviço
Serviços SHALL se comunicar usando nomes de serviço do Docker; endereços IP MUST NOT ser fixados em código ou configuração.

#### Scenario: Endereços internos
- **WHEN** a configuração de qualquer serviço referencia outro serviço da stack
- **THEN** a referência usa o nome do serviço (ex.: `http://litellm:4000`), nunca um IP

### Requirement: Segmentação de redes
Os serviços SHALL ser distribuídos em redes Docker separadas por função (frontend, IA, observabilidade, dados), de modo que cada serviço alcance apenas aquilo de que precisa.

#### Scenario: Portal isolado de modelos e bancos de terceiros
- **WHEN** o container do LibreChat tenta abrir conexão com a IA Local, o Presidio ou os bancos do Langfuse/LiteLLM
- **THEN** a conexão falha por não haver rota de rede entre eles

### Requirement: Exposição mínima de portas
Somente as interfaces de uso humano (portal de chat e interface do Langfuse) SHALL ser publicadas no host. Bancos, cache, armazenamento de objetos, Presidio e IA Local MUST NOT ser publicados no host.

#### Scenario: Portas publicadas
- **WHEN** as portas publicadas pela stack são inspecionadas
- **THEN** apenas o portal de chat, a interface do Langfuse e, opcionalmente, o gateway em `127.0.0.1` aparecem publicados

### Requirement: Secrets fora do versionamento
Secrets SHALL ser fornecidos por variáveis de ambiente lidas de um arquivo `.env` não versionado. O repositório MUST conter `.env.example` sem valores reais, e o `.env` MUST estar no `.gitignore`.

#### Scenario: Repositório sem secrets
- **WHEN** o conteúdo versionado é inspecionado
- **THEN** não há chaves de API, senhas ou tokens reais, e `.env` não está rastreado

#### Scenario: Secret obrigatório ausente
- **WHEN** a stack é iniciada sem um secret obrigatório definido no `.env`
- **THEN** a inicialização falha com mensagem indicando a variável ausente, em vez de usar um valor padrão inseguro

### Requirement: Persistência em volumes
Dados de bancos, armazenamento de objetos e modelos da IA Local SHALL ser persistidos em volumes nomeados, sobrevivendo a recriação dos containers.

#### Scenario: Recriação de containers
- **WHEN** `docker compose down` seguido de `docker compose up -d` é executado (sem `-v`)
- **THEN** usuários, conversas, configurações e modelos baixados continuam disponíveis
