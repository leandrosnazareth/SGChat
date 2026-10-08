#!/usr/bin/env bash
# Testes de fumaça de cotas e budgets (spec usage-quotas), contra o gateway real.
# Usa políticas temporárias em litellm/policies/quotas.yaml (restauradas ao fim) e
# um usuário de teste por caso. Regras com limite 0 provocam fallback/bloqueio sem
# chamar o Google; só o caso de contabilização faz 1 chamada real ao gemini.
# Uso: tests/smoke/quotas.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
set -a; . ./.env; set +a

GW="http://127.0.0.1:${LITELLM_PORT:-4000}"
POLICY=litellm/policies/quotas.yaml
BACKUP="$(mktemp)"; cp -p "$POLICY" "$BACKUP"
TMP="$(mktemp)"
trap 'cp -p "$BACKUP" "$POLICY"; rm -f "$BACKUP" "$TMP"' EXIT
RUN="q-$(date +%s)"
PASS=0; FAIL=0
ok()  { echo "  ✔ $*"; PASS=$((PASS + 1)); }
bad() { echo "  ✘ $*"; FAIL=$((FAIL + 1)); }

policy() { # <regras YAML (lista)> — escreve uma política temporária
  cat > "$POLICY" <<EOF
version: 1
fallback_model: local-ai
unlimited_models: [local-ai]
rules:
$1
EOF
}
chat() { # <e-mail> <modelo> → "<http_code> <error.message|->"
  : > "$TMP"
  local code
  code=$(curl -s -m 120 -o "$TMP" -w '%{http_code}' "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $LITELLM_PORTAL_KEY" -H 'Content-Type: application/json' \
    -H "x-litellm-end-user-id: $1" \
    -d "{\"model\":\"$2\",\"max_tokens\":20,\"messages\":[{\"role\":\"user\",\"content\":\"diga ok\"}]}")
  echo "$code $(python3 -c "import json;print(json.load(open('$TMP')).get('error',{}).get('message','-'))" 2>/dev/null || echo -)"
}
routing() { # <e-mail> → "model_group|routing_reason|requested|effective|quota_rule" do último registro (aguarda gravação)
  local row=""
  for _ in $(seq 1 25); do
    row=$(docker compose exec -T postgres psql -U postgres -d litellm -tA -c \
      "select model_group||'|'||coalesce(metadata::jsonb->'spend_logs_metadata'->>'routing_reason','')||'|'||coalesce(metadata::jsonb->'spend_logs_metadata'->>'requested_model','')||'|'||coalesce(metadata::jsonb->'spend_logs_metadata'->>'effective_model','')||'|'||coalesce(metadata::jsonb->'spend_logs_metadata'->>'quota_rule','') from \"LiteLLM_SpendLogs\" where end_user='$1' and status='success' order by \"startTime\" desc limit 1")
    [[ -n "$row" ]] && break
    sleep 2
  done
  echo "$row"
}
expect_route() { # <descrição> <esperado> <obtido>
  [[ "$3" == "$2" ]] && ok "$1 → $3" || bad "$1 → esperado [$2], obtido [$3]"
}

echo "== Fallback para a IA Local (sem chamar o Google)"
U1="$RUN-tokens@smoke.local"
policy "  - {name: zero-tokens, applies_to: \"user:$U1\", models: [gemini], period: daily, max_tokens: 0}"
read -r code _ <<<"$(chat "$U1" gemini)"
[[ "$code" == 200 ]] && ok "cota de tokens esgotada → 200 (atendido)" || bad "cota de tokens esgotada → HTTP $code"
expect_route "registro de uso" "local-ai|TOKEN_QUOTA_EXCEEDED|gemini|local-ai|zero-tokens" "$(routing "$U1")"

U2="$RUN-budget@smoke.local"
policy "  - {name: zero-dolar, applies_to: all_users, models: external, period: monthly, max_cost_usd: 0}"
read -r code _ <<<"$(chat "$U2" gemini)"
[[ "$code" == 200 ]] && ok "budget esgotado → 200 (atendido)" || bad "budget esgotado → HTTP $code"
expect_route "registro de uso" "local-ai|BUDGET_EXCEEDED|gemini|local-ai|zero-dolar" "$(routing "$U2")"

echo "== Bloqueio"
U3="$RUN-block@smoke.local"
policy "  - {name: bloqueia, applies_to: \"user:$U3\", models: [gemini], period: weekly, max_tokens: 0, action: BLOCK}"
read -r code msg <<<"$(chat "$U3" gemini)"
[[ "$code" == 429 && "$msg" == TOKEN_QUOTA_EXCEEDED ]] && ok "action BLOCK → 429 TOKEN_QUOTA_EXCEEDED" || bad "action BLOCK → $code $msg"

echo "== IA Local sem cota"
read -r code _ <<<"$(chat "$U3" local-ai)"
[[ "$code" == 200 ]] && ok "mesmo usuário → local-ai → 200" || bad "local-ai → HTTP $code"
expect_route "registro de uso" "local-ai|DIRECT|local-ai|local-ai|" "$(routing "$U3")"

echo "== Fail-closed com política de cotas inválida"
U4="$RUN-invalid@smoke.local"
printf 'version: 1\nrules: [quebrado\n' > "$POLICY"
read -r code _ <<<"$(chat "$U4" gemini)"
[[ "$code" == 200 ]] && ok "gemini com política inválida → 200 (atendido localmente)" || bad "política inválida → HTTP $code"
expect_route "registro de uso" "local-ai|QUOTA_CHECK_UNAVAILABLE|gemini|local-ai|" "$(routing "$U4")"

echo "== Contabilização imediata (1 chamada real ao gemini)"
U5="$RUN-count@smoke.local"
policy "  - {name: um-token, applies_to: \"user:$U5\", models: [gemini], period: daily, max_tokens: 1}"
read -r code msg <<<"$(chat "$U5" gemini)"
if [[ "$code" == 200 ]]; then
  ok "1ª chamada (consumo 0 < 1) → gemini direto"
  read -r code _ <<<"$(chat "$U5" gemini)"     # logo em seguida: consumo ainda só na memória
  [[ "$code" == 200 ]] || bad "2ª chamada → HTTP $code"
  expect_route "2ª chamada, imediata" "local-ai|TOKEN_QUOTA_EXCEEDED|gemini|local-ai|um-token" "$(routing "$U5")"
else
  bad "1ª chamada ao gemini falhou no provedor (HTTP $code ${msg:0:80}); contabilização não verificada"
fi

echo "== Política restaurada"
cp -p "$BACKUP" "$POLICY"; touch "$POLICY"
git diff --quiet -- "$POLICY" && ok "quotas.yaml igual ao versionado" || bad "quotas.yaml diferente do versionado"

echo
echo "Resultado: $PASS ok, $FAIL falha(s)"
[[ "$FAIL" -eq 0 ]]
