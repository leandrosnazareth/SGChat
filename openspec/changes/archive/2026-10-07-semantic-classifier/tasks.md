# Tasks

## 1. Configuração e regras anti-manipulação

- [x] 1.1 Adicionar `SECURITY_CONFIDENCE_THRESHOLD=0.80` ao `.env.example` (e ao `.env` via `scripts/init-env.sh`) e ao serviço `litellm` no compose; verificar com `docker compose config`
- [x] 1.2 Adicionar ao `security.yaml` a seção `classifier:` (D7) e a regra `tentativa-de-manipulacao` (D6); verificar que o YAML é válido e a regex compila

## 2. Classificador

- [x] 2.1 Implementar o `LocalAiSecurityClassifier` em `security_router.py` (D1–D5: quando roda, chamada ao Ollama com schema, validação e mapeamento, limiar, teto, pedaços, cache, fail-closed, metadados sem `reasons`) e a validação da seção `classifier:` na política; verificar que o módulo importa e a política carrega no container
- [x] 2.2 Atualizar `tests/unit/test_security_router.py` com os casos do D8 e verificar que todos (antigos e novos) passam
- [x] 2.3 Reiniciar o `litellm` e verificar no log a política carregada com o classificador ativo

## 3. Avaliação e validação

- [x] 3.1 Criar `tests/eval/classifier_cases.json` e `tests/eval/classifier_eval.py` (D8) e verificar, contra a IA Local real, **zero vazamentos**; registrar matriz, falsos positivos e latência em `docs/testing.md`
- [x] 3.2 Criar `tests/smoke/semantic.sh` (D8, incluindo IA Local parada e restaurada) e verificar que passa com no máximo 1 chamada real ao Gemini
- [x] 3.3 Validar no navegador: Ana escolhe `gemini` e envia um texto sensível sem padrão (ex.: aquisição em negociação) → resposta da IA Local e registro com `classifier:*`; uma pergunta técnica genérica → `gemini`. Registrar em `docs/testing.md`

## 4. Documentação e regressão

- [x] 4.1 Atualizar `docs/security.md` (classificador, limiar, teto, manipulação, avaliação, como desligar/calibrar), `docs/architecture.md` e `README.md`; verificar seguindo o guia para desligar e religar o classificador sem reiniciar
- [x] 4.2 Rodar `scripts/healthcheck.sh`, todos os testes unitários, a avaliação e os smoke tests (`gateway`, `model-access`, `quotas`, `rate-limit`, `security`, `semantic`, `network-isolation`); verificar que todos passam e registrar em `docs/testing.md`

## Workflow follow-up

- Arquivar com `/opsx:archive` após a validação; em seguida, planejar a Fase 7 (modo AUTO / smart routing).
