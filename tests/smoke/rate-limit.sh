#!/usr/bin/env bash
# Testes de fumaça do rate limit por usuário final (spec rate-limits), contra o
# gateway real. Um usuário de teste único dispara RATE_LIMIT_RPM requisições
# baratas a local-ai (max_tokens 1) e mais uma: a excedente deve receber 429.
# Outro usuário, na mesma janela, deve continuar sendo atendido.
# Não usa o Gemini. Uso: tests/smoke/rate-limit.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
set -a; . ./.env; set +a

GW="http://127.0.0.1:${LITELLM_PORT:-4000}"
RPM="${RATE_LIMIT_RPM:-30}"
RUN="rl-$(date +%s)"
HEAVY="$RUN-a@smoke.local"
OTHER="$RUN-b@smoke.local"
PASS=0; FAIL=0
ok()  { echo "  ✔ $*"; PASS=$((PASS + 1)); }
bad() { echo "  ✘ $*"; FAIL=$((FAIL + 1)); }

call() { # <e-mail> → status HTTP
  curl -s -o /dev/null -m 60 -w '%{http_code}' "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $LITELLM_PORTAL_KEY" -H 'Content-Type: application/json' \
    -H "x-litellm-end-user-id: $1" \
    -d '{"model":"local-ai","max_tokens":1,"messages":[{"role":"user","content":"ok"}]}'
}

echo "== RPM por usuário final (limite $RPM)"
codes=()
for _ in $(seq 1 "$RPM"); do codes+=("$(call "$HEAVY")"); done
within=$(printf '%s\n' "${codes[@]}" | grep -c '^200$')
[[ "$within" == "$RPM" ]] && ok "$RPM requisições dentro do limite → todas 200" \
  || bad "dentro do limite: $within de $RPM com 200 (códigos: $(printf '%s ' "${codes[@]}" | tr -s ' '))"
code=$(call "$HEAVY")
[[ "$code" == 429 ]] && ok "requisição $((RPM + 1)) no mesmo minuto → 429" || bad "requisição $((RPM + 1)) → esperado 429, obtido $code"
code=$(call "$OTHER")
[[ "$code" == 200 ]] && ok "outro usuário na mesma janela → 200" || bad "outro usuário → esperado 200, obtido $code"

echo
echo "Resultado: $PASS ok, $FAIL falha(s)"
[[ "$FAIL" -eq 0 ]]
