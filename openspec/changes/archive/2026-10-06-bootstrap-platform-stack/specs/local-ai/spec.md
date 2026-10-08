# Spec Delta

## Purpose

Disponibiliza a IA Local da empresa como serviço self-hosted e containerizado, acessível somente pela rede interna e por API OpenAI-compatible, para que conteúdo processado por ela nunca saia da infraestrutura.

## ADDED Requirements

### Requirement: API OpenAI-compatible interna
A IA Local SHALL expor `GET /v1/models` e `POST /v1/chat/completions` compatíveis com a API OpenAI na rede interna da plataforma.

#### Scenario: Chamada interna
- **WHEN** o gateway chama `POST /v1/chat/completions` na IA Local pelo nome do serviço
- **THEN** recebe uma resposta no formato OpenAI gerada pelo modelo local

### Requirement: Acesso restrito à rede interna
A IA Local MUST NOT ser publicada no host nem alcançável por serviços fora da rede de IA; o único consumidor previsto é o gateway.

#### Scenario: Tentativa de acesso externo
- **WHEN** alguém tenta acessar a IA Local a partir do host ou do container do portal
- **THEN** a conexão não é possível

### Requirement: Modelo provisionado automaticamente
O modelo configurado SHALL ser baixado automaticamente na primeira subida e persistido em volume, sem passos manuais no host. O nome do modelo MUST ser configurável por variável de ambiente.

#### Scenario: Primeira subida
- **WHEN** a stack sobe pela primeira vez com volume de modelos vazio
- **THEN** o modelo configurado é baixado e a IA Local passa a responder sem intervenção manual

#### Scenario: Subidas seguintes
- **WHEN** a stack é reiniciada com o volume de modelos já populado
- **THEN** o modelo não é baixado novamente

### Requirement: Saúde reflete prontidão do modelo
A IA Local SHALL ser reportada como saudável somente quando o modelo configurado estiver disponível para inferência.

#### Scenario: Modelo ainda baixando
- **WHEN** o download do modelo ainda não terminou
- **THEN** o serviço não é reportado como saudável e o gateway não o considera pronto

### Requirement: Execução sem GPU
A IA Local SHALL funcionar em CPU, sem exigir GPU ou runtime de GPU no host.

#### Scenario: Host sem runtime NVIDIA
- **WHEN** a stack sobe em host sem `nvidia-container-toolkit`
- **THEN** a IA Local inicia e responde normalmente usando CPU

### Requirement: Aceleração por GPU quando disponível
Quando o host possuir GPU NVIDIA com runtime de containers instalado e o modo GPU estiver habilitado na configuração, a IA Local SHALL executar a inferência na GPU, sem alterar a API exposta nem exigir comandos diferentes de subida.

#### Scenario: Modo GPU habilitado
- **WHEN** a stack sobe com o modo GPU habilitado em host com `nvidia-container-toolkit`
- **THEN** o modelo é carregado na GPU e as respostas continuam no mesmo formato OpenAI

#### Scenario: Modo GPU habilitado sem runtime disponível
- **WHEN** o modo GPU está habilitado mas o host não possui runtime NVIDIA
- **THEN** a subida do serviço de IA Local falha com erro explícito do Docker, e nenhum outro serviço passa a atender as requisições de `local-ai` em seu lugar
