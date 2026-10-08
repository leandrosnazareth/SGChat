#!/usr/bin/env bash
# Cria um usuário no LibreChat (o auto-registro está desabilitado).
#
#   scripts/create-user.sh <email> <nome> <username> [--save]
#
# A senha vem da variável PASSWORD ou é gerada aleatoriamente. Com --save, as
# credenciais são gravadas em .test-users (arquivo local, ignorado pelo git) em
# vez de exibidas no terminal.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ $# -lt 3 ]]; then
  echo "uso: $0 <email> <nome> <username> [--save]" >&2
  exit 2
fi
email="$1"; name="$2"; username="$3"; save="${4:-}"
password="${PASSWORD:-$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)}"

docker compose exec -T librechat npm run --silent create-user -- \
  "$email" "$name" "$username" "$password" --email-verified=true </dev/null

if [[ "$save" == "--save" ]]; then
  umask 077
  echo "${email} ${password}" >> .test-users
  echo "create-user: credenciais de ${email} gravadas em .test-users"
else
  echo "create-user: senha de ${email}: ${password}"
fi
