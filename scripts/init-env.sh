#!/usr/bin/env bash
# Cria ou completa o .env a partir do .env.example.
# - Variáveis já presentes no .env nunca são alteradas.
# - Variáveis ausentes são adicionadas; as marcadas [secret] recebem valor aleatório.
set -euo pipefail

cd "$(dirname "$0")/.."
EXAMPLE=.env.example
ENV_FILE=.env

umask 077
touch "$ENV_FILE"

secret_for() {
  case "$1" in
    LITELLM_MASTER_KEY|LITELLM_PORTAL_KEY) echo "sk-$(openssl rand -hex 32)" ;;
    LANGFUSE_INIT_PROJECT_PUBLIC_KEY)      echo "pk-lf-$(openssl rand -hex 16)" ;;
    LANGFUSE_INIT_PROJECT_SECRET_KEY)      echo "sk-lf-$(openssl rand -hex 16)" ;;
    LIBRECHAT_CREDS_IV|LANGFUSE_INIT_USER_PASSWORD) openssl rand -hex 16 ;;
    *)                                     openssl rand -hex 32 ;;
  esac
}

added=0
while IFS= read -r line; do
  [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)=([^#]*)(#.*)?$ ]] || continue
  name="${BASH_REMATCH[1]}"
  value="${BASH_REMATCH[2]}"
  value="${value%"${value##*[![:space:]]}"}"   # remove espaços finais, preserva aspas
  comment="${BASH_REMATCH[3]:-}"
  grep -qE "^${name}=" "$ENV_FILE" && continue
  if [[ "$comment" == *"[secret]"* && -z "$value" ]]; then
    value="$(secret_for "$name")"
  fi
  echo "${name}=${value}" >> "$ENV_FILE"
  added=$((added + 1))
done < "$EXAMPLE"

echo "init-env: ${added} variável(is) adicionada(s) a ${ENV_FILE}."
missing=$(grep -E '^GEMINI_API_KEY=$' "$ENV_FILE" || true)
[[ -n "$missing" ]] && echo "init-env: aviso — GEMINI_API_KEY vazia; o modelo 'gemini' não funcionará."
exit 0
