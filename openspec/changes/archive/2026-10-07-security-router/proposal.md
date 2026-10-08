# Proposal

## Why

Hoje qualquer conteúdo enviado ao modelo `gemini` sai da empresa sem análise: contratos, CPFs, senhas ou chaves de API chegam ao Google se o usuário escolher o modelo externo. O princípio central do documento de software é que **dados sensíveis não saem da infraestrutura corporativa**. Esta mudança implementa a **Fase 5** do roadmap (`prompt.txt`, seções 4, 6, 7, 9, 13 e 28): `SecurityRouter`, `SensitiveDataDetector` e `PolicyEngine` com regex, regras customizadas e Presidio, interceptando o conteúdo antes da chamada externa. A classificação semântica pela IA Local fica para a Fase 6.

## What Changes

- **Classificação de todo o contexto** enviado ao modelo (mensagem atual, histórico, system prompt, partes de texto, argumentos e resultados de ferramentas) em `PUBLIC`, `INTERNAL`, `CONFIDENTIAL` ou `RESTRICTED`, sempre pela evidência **mais restritiva**.
- **Detectores configuráveis** em `litellm/policies/security.yaml`, sem regras empresariais no código:
  - regex com validação opcional: CPF e CNPJ com dígito verificador, telefone BR, chaves de API e tokens (OpenAI, AWS, Google, GitHub, JWT, Bearer, chave privada), credenciais declaradas ("senha: …"), IP e URL internos, números de contrato, valores em R$, blocos de código-fonte;
  - palavras-chave (`CONFIDENCIAL`, `RESTRITO`, `USO INTERNO`…), sem diferenciar maiúsculas e acentos;
  - clientes e projetos classificados;
  - entidades do Presidio (e-mail, cartão de crédito, IBAN, criptomoeda), mapeadas para classificações.
- **Decisão por classificação** quando o modelo pedido é externo:
  - `PUBLIC` → segue;
  - `INTERNAL` → IA Local (padrão, configurável);
  - `CONFIDENTIAL` → IA Local, **mesmo que o usuário tenha escolhido o modelo externo**;
  - `RESTRICTED` → bloqueio (padrão) ou IA Local.

  Conteúdo `RESTRICTED` é bloqueado também quando pedido à IA Local (configurável).
- **Fail-closed**: Presidio indisponível, política inválida ou erro na análise → o conteúdo **não sai** (IA Local).
- **Registro** no uso do gateway: `classification`, `external_allowed`, `security_reasons` (nomes das regras, **nunca o texto encontrado**), `requested_model`, `effective_model` e `routing_reason=SECURITY_POLICY`.
- Ordem de avaliação no gateway: permissão (ACL) → segurança → cota. A IA Local indisponível **nunca** resulta em envio à nuvem.
- Testes unitários e de fumaça (os testes 1, 2, 3, 5 e 10 do `prompt.txt`), validação no navegador e `docs/security.md`.

### Fora do escopo

- Classificador semântico pela IA Local e `SECURITY_CONFIDENCE_THRESHOLD` (Fase 6). Nesta fase, a confiança das regras determinísticas é 1,0.
- Mascaramento/anonimização (Presidio anonymizer) e envio parcial: o conteúdo inteiro vai para a IA Local ou é bloqueado.
- Análise do conteúdo binário de anexos. Anexos que o portal envia como texto nas mensagens são analisados.
- Modo AUTO (Fase 7) e metadados no Langfuse (Fase 8).

### Impacto em segurança

É a principal barreira de proteção de dados da plataforma. Falsos positivos levam o conteúdo para a IA Local, o que é seguro mas pode reduzir a qualidade da resposta. Falsos negativos vazam dados. Por isso o desenho privilegia o conservador e registra as razões para ajuste das regras. O reconhecimento de nomes, lugares e datas do Presidio (só em inglês na imagem atual) gera falsos positivos em português ("Explique Virtual Threads em Java" → `PERSON`) e fica desligado por padrão.

## Capabilities

### New Capabilities

- `content-security`: classificação do conteúdo enviado aos modelos, detectores configuráveis, decisão de roteamento por classificação, bloqueio de conteúdo restrito, fail-closed e registro das decisões.

### Modified Capabilities

(nenhuma)

## Impact

- **Novos**: `litellm/custom/security_router.py`, `litellm/policies/security.yaml`, `tests/unit/test_security_router.py`, `tests/smoke/security.sh`, `docs/security.md`.
- **Alterados**: `litellm/config.yaml` (callback na ordem ACL → segurança → cota), `litellm/custom/quota_policy.py` (preserva `requested_model`/`routing_reason` gravados pela segurança), `docker-compose.yml` (variável `SECURITY_POLICY_PATH`), `README.md`, `docs/architecture.md`, `docs/testing.md`.
- **Comportamento visível**: mensagens com conteúdo sensível escolhendo `gemini` passam a ser respondidas pela IA Local; mensagens com segredos são recusadas com `SECURITY_POLICY_BLOCKED`.
