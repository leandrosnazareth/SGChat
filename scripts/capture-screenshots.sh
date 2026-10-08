#!/usr/bin/env bash
# Gera as telas do README (assets/screenshots/) a partir da stack em execução, com o Playwright
# oficial em container (nada é instalado no host). Usa os usuários de teste de .test-users
# (scripts/create-user.sh … --save) e a conta do Langfuse do .env; as senhas só passam por
# variável de ambiente. Cria conversas reais (1 pergunta pública vai ao gemini).
# Uso: scripts/capture-screenshots.sh [chat,langfuse,audit]
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
[[ -f .test-users ]] || { echo "crie os usuários de teste antes (docs/audit.md, README)"; exit 2; }
CREDENTIALS=$(python3 -c '
import json, sys
creds = {}
for line in open(".test-users"):
    parts = line.split()
    if len(parts) == 2 and "@" in parts[0] and not line.startswith("#"):
        creds[parts[0]] = parts[1]
print(json.dumps(creds))')
mkdir -p assets/screenshots
docker run --rm --network host --ipc host -u "$(id -u):$(id -g)" \
  -e HOME=/tmp -e CREDENTIALS="$CREDENTIALS" -e ONLY="${1:-}" \
  -e LANGFUSE_EMAIL="$LANGFUSE_INIT_USER_EMAIL" -e LANGFUSE_PASSWORD="$LANGFUSE_INIT_USER_PASSWORD" \
  -e LANGFUSE_PK="$LANGFUSE_INIT_PROJECT_PUBLIC_KEY" -e LANGFUSE_SK="$LANGFUSE_INIT_PROJECT_SECRET_KEY" \
  -e CHAT_URL="http://localhost:${LIBRECHAT_PORT:-3080}" -e LANGFUSE_URL="http://localhost:${LANGFUSE_PORT:-3000}" \
  -e AUDIT_URL="http://localhost:${AUDIT_PORTAL_PORT:-3090}" \
  -v "$PWD/scripts/screenshots:/opt/capture:ro" -v "$PWD/assets/screenshots:/work/assets/screenshots" \
  mcr.microsoft.com/playwright/python:v1.63.0-noble \
  bash -c 'pip install --quiet --user playwright==1.63.0 2>/dev/null; python3 /opt/capture/capture.py'
