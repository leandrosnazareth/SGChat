#!/usr/bin/env bash
# Avaliação do classificador de segurança (regras + Presidio + IA Local reais).
# Critério: zero vazamentos (caso não-PUBLIC liberado ao modelo externo).
# Uso: tests/eval/classifier-eval.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
docker compose exec -T -e CASES="$(cat tests/eval/classifier_cases.json)" litellm python3 - < tests/eval/classifier_eval.py
