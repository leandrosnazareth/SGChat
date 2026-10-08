#!/usr/bin/env bash
# Testes de fumaça do modelo "auto" (spec auto-routing), contra o gateway real.
# Só o caso "complexo e público (Ana)" chama o Google (1 chamada); a exclusão por cota
# usa uma regra temporária de limite 0 em quotas.yaml (restaurada ao fim). Se houver pausa recente
# do circuit breaker (ex.: logo após security.sh/semantic.sh), espera ela expirar.
# Uso: tests/smoke/auto.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
set -a; . ./.env; set +a

GW="http://127.0.0.1:${LITELLM_PORT:-4000}"
QUOTAS=litellm/policies/quotas.yaml
BACKUP="$(mktemp)"; cp -p "$QUOTAS" "$BACKUP"
TMP="$(mktemp)"
trap 'cp -p "$BACKUP" "$QUOTAS"; rm -f "$BACKUP" "$TMP"' EXIT
RUN="a-$(date +%s)"
ANA=ana.teste@corporate-ai.local
BRUNO=bruno.teste@corporate-ai.local
PASS=0; FAIL=0
ok()  { echo "  ✔ $*"; PASS=$((PASS + 1)); }
bad() { echo "  ✘ $*"; FAIL=$((FAIL + 1)); }

SIMPLE="Quanto é 15% de 200?"
# Pública, ≥ 800 caracteres e com termos de análise → faixa complex (score 4)
COMPLEX="Compare detalhadamente as arquiteturas de microsserviços e monolito modular para um sistema de comércio eletrônico, considerando escalabilidade, custo operacional, complexidade de implantação, observabilidade e organização das equipes. $(printf 'Considere também cenários de crescimento de tráfego sazonal e a maturidade do time em práticas de entrega contínua. %.0s' 1 2 3 4 5 6) Responda em uma frase."

chat() { # <e-mail> <texto> → código HTTP
  : > "$TMP"
  python3 -c 'import json,sys;print(json.dumps({"model":"auto","max_tokens":20,"messages":[{"role":"user","content":sys.argv[1]}]}))' "$2" \
    | curl -s -m 180 -o "$TMP" -w '%{http_code}' "$GW/v1/chat/completions" \
        -H "Authorization: Bearer $LITELLM_PORTAL_KEY" -H 'Content-Type: application/json' \
        -H "x-litellm-end-user-id: $1" -d @-
}
record() { # <e-mail> <desde (epoch)> → "model_group|routing_reason|requested|effective|tier|chosen|excluded(json)"
  local row=""
  for _ in $(seq 1 25); do
    row=$(docker compose exec -T postgres psql -U postgres -d litellm -tA -c \
      "select model_group||'|'||coalesce(m->>'routing_reason','')||'|'||coalesce(m->>'requested_model','')||'|'||coalesce(m->>'effective_model','')||'|'||coalesce(m->'auto_decision'->>'tier','')||'|'||coalesce(m->'auto_decision'->>'chosen','')||'|'||coalesce(m->'auto_decision'->>'excluded','')
       from (select model_group, \"startTime\", metadata::jsonb->'spend_logs_metadata' m from \"LiteLLM_SpendLogs\"
             where end_user='$1' and status='success' and \"startTime\" > to_timestamp($2)) t order by \"startTime\" desc limit 1")
    [[ -n "$row" ]] && break
    sleep 2
  done
  echo "$row"
}
expect() { # <descrição> <esperado> <obtido>
  [[ "$3" == "$2" ]] && ok "$1 → $3" || bad "$1 → esperado [$2], obtido [$3]"
}
models() {
  curl -s -m 30 "$GW/v1/models" -H "Authorization: Bearer $LITELLM_PORTAL_KEY" -H "x-litellm-end-user-id: $1" \
    | python3 -c "import json,sys;print(' '.join(sorted(m['id'] for m in json.load(sys.stdin)['data'])))" 2>/dev/null || echo "(erro)"
}

# security.sh e semantic.sh param a IA Local; as falhas pausam o modelo no circuit breaker do AUTO
# (comportamento esperado). Aguarda o fim de uma pausa recente para não depender da ordem dos testes.
COOLDOWN=$(sed -n 's/^ *cooldown_seconds: *\([0-9]*\).*/\1/p' litellm/policies/routing.yaml)
for _ in $(seq 1 20); do
  # captura antes do grep: com pipefail, grep -q + SIGPIPE no docker daria falso "sem pausa"
  recent=$(docker compose logs --since "$((COOLDOWN + 5))s" --no-log-prefix litellm 2>&1)
  grep -q "model_router: .* pausado" <<<"$recent" || break
  echo "  … modelo pausado pelo circuit breaker; aguardando ${COOLDOWN}s"; sleep 15
done

echo "== Listagem"
expect "Ana (DEVELOPER) lista" "auto gemini local-ai" "$(models "$ANA")"
expect "Bruno (FINANCE) lista" "auto local-ai" "$(models "$BRUNO")"

echo "== Pergunta simples → IA Local"
U="$RUN-simples@smoke.local"; t=$(date +%s)
code=$(chat "$U" "$SIMPLE"); [[ "$code" == 200 ]] || bad "simples → HTTP $code"
expect "registro" "local-ai|AUTO|auto|local-ai|simple|local-ai|{}" "$(record "$U" "$t")"

echo "== Complexo, sem permissão para o externo (Bruno) → IA Local"
t=$(date +%s)
code=$(chat "$BRUNO" "$COMPLEX"); [[ "$code" == 200 ]] || bad "Bruno complexo → HTTP $code"
expect "registro" 'local-ai|AUTO|auto|local-ai|complex|local-ai|{"gemini": "permission"}' "$(record "$BRUNO" "$t")"

echo "== Complexo com cota do gemini esgotada → IA Local (sem chamar o Google)"
U="$RUN-cota@smoke.local"; t=$(date +%s)
cat >> "$QUOTAS" <<EOF
  - {name: smoke-auto-zero, applies_to: "user:$U", models: [gemini], period: daily, max_tokens: 0}
EOF
code=$(chat "$U" "$COMPLEX"); [[ "$code" == 200 ]] || bad "cota zero → HTTP $code"
expect "registro" 'local-ai|AUTO|auto|local-ai|complex|local-ai|{"gemini": "quota"}' "$(record "$U" "$t")"
cp -p "$BACKUP" "$QUOTAS"; touch "$QUOTAS"

echo "== Segurança decide antes do auto"
U="$RUN-conf@smoke.local"; t=$(date +%s)
code=$(chat "$U" "Compare detalhadamente o faturamento do cliente XPTO com o do ano passado.")
[[ "$code" == 200 ]] || bad "confidencial → HTTP $code"
expect "confidencial → registro" "local-ai|SECURITY_POLICY|auto|local-ai|||" "$(record "$U" "$t")"
code=$(chat "$RUN-rest@smoke.local" "A senha do servidor de produção é Prod@2026! Compare as opções de rotação.")
msg=$(python3 -c "import json;print(json.load(open('$TMP')).get('error',{}).get('message','-'))" 2>/dev/null)
[[ "$code" == 403 && "$msg" == SECURITY_POLICY_BLOCKED ]] && ok "restrito → 403 SECURITY_POLICY_BLOCKED" || bad "restrito → $code $msg"

echo "== Complexo e público (Ana) → gemini (1 chamada real)"
t=$(date +%s)
code=$(chat "$ANA" "$COMPLEX")
if [[ "$code" == 200 ]]; then
  expect "registro" "gemini|AUTO|auto|gemini|complex|gemini|{}" "$(record "$ANA" "$t")"
else
  bad "Ana complexo → HTTP $code ($(head -c 160 "$TMP"))"
fi

echo "== Política restaurada"
git diff --quiet -- "$QUOTAS" && ok "quotas.yaml igual ao versionado" || bad "quotas.yaml diferente do versionado"

echo
echo "Resultado: $PASS ok, $FAIL falha(s)"
[[ "$FAIL" -eq 0 ]]
