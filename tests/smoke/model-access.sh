#!/usr/bin/env bash
# Testes de fumaça da política de acesso a modelos (spec model-access), contra o
# gateway real em 127.0.0.1:${LITELLM_PORT}, usando a chave do portal. A identidade
# vai em x-litellm-end-user-id (e-mail), como o portal envia.
# Altera temporariamente litellm/policies/model-access.yaml para testar a
# recarga sem reinício e o fail-closed; o arquivo original é restaurado ao fim.
# Economia de cota do Gemini (plano gratuito: 20 req/dia por modelo): só UMA
# chamada real ao gemini (Ana); os demais casos usam local-ai ou a listagem, que
# exercitam a mesma decisão da política sem chamar o provedor.
# Uso: tests/smoke/model-access.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
set -a; . ./.env; set +a

GW="http://127.0.0.1:${LITELLM_PORT:-4000}"
POLICY=litellm/policies/model-access.yaml
BACKUP="$(mktemp)"; cp -p "$POLICY" "$BACKUP"
TMP="$(mktemp)"
trap 'cp -p "$BACKUP" "$POLICY"; rm -f "$BACKUP" "$TMP"' EXIT
SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

ANA=ana.teste@corporate-ai.local         # DEVELOPER: local-ai, gemini
BRUNO=bruno.teste@corporate-ai.local     # FINANCE: local-ai
UNKNOWN="desconhecido-$(date +%s)@corporate-ai.local"   # fora da política → USER

PASS=0; FAIL=0
ok()  { echo "  ✔ $*"; PASS=$((PASS + 1)); }
bad() { echo "  ✘ $*"; FAIL=$((FAIL + 1)); }

# chat <chave> <modelo> [e-mail] → imprime "<http_code> <error.message|->"
chat() {
  local headers=(-H "Authorization: Bearer $1" -H 'Content-Type: application/json')
  [[ -n "${3:-}" ]] && headers+=(-H "x-litellm-end-user-id: $3")
  local code
  : > "$TMP"   # nunca reaproveitar a resposta da chamada anterior
  code=$(curl -s -m 120 -o "$TMP" -w '%{http_code}' "$GW/v1/chat/completions" "${headers[@]}" \
    -d "{\"model\":\"$2\",\"max_tokens\":40,\"messages\":[{\"role\":\"user\",\"content\":\"diga ok\"}]}")
  if [[ "$code" == 000 ]]; then echo "000 sem-resposta-do-gateway(curl)"; return; fi
  echo "$code $(python3 -c "import json;d=json.load(open('$TMP'));print(d.get('error',{}).get('message','-'))" 2>/dev/null || echo -)"
}
# models <chave> [e-mail] → imprime os ids listados em /v1/models, ordenados
models() {
  local headers=(-H "Authorization: Bearer $1")
  [[ -n "${2:-}" ]] && headers+=(-H "x-litellm-end-user-id: $2")
  curl -s -m 30 "$GW/v1/models" "${headers[@]}" \
    | python3 -c "import json,sys;print(' '.join(sorted(m['id'] for m in json.load(sys.stdin)['data'])) or '(vazia)')" 2>/dev/null || echo "(erro)"
}
expect_list() { # descrição, esperado, obtido
  [[ "$3" == "$2" ]] && ok "$1 → [$3]" || bad "$1 → esperado [$2], obtido [$3]"
}
status_of() { # <chave> <caminho> [e-mail] → status HTTP
  local headers=(-H "Authorization: Bearer $1")
  [[ -n "${3:-}" ]] && headers+=(-H "x-litellm-end-user-id: $3")
  curl -s -o /dev/null -m 30 -w '%{http_code}' "$GW$2" "${headers[@]}"
}
expect() { # descrição, esperado ("allow" ou "deny"), resultado de chat()
  # allow: a requisição passou pela política — 200, ou erro devolvido pelo próprio
  #        provedor (ex.: cota/instabilidade do Gemini), que prova que o ACL liberou.
  # deny:  HTTP 403 com error.message == MODEL_ACCESS_DENIED.
  local desc="$1" want="$2" code msg
  read -r code msg <<<"$3"
  if [[ "$want" == allow && "$code" == 200 ]]; then
    ok "$desc → 200 (autorizado)"
  elif [[ "$want" == allow && "$code" != 403 && "$msg" == *[Gg]eminiException* ]]; then
    ok "$desc → autorizado; provedor respondeu $code ($(grep -oE 'RateLimitError|ServiceUnavailableError' <<<"$msg" | head -1))"
  elif [[ "$want" == deny && "$code" == 403 && "$msg" == MODEL_ACCESS_DENIED ]]; then
    ok "$desc → 403 MODEL_ACCESS_DENIED"
  else
    bad "$desc → esperado $want, obtido $code ${msg:0:120}"
  fi
}
rewrite_policy() { # aplica uma substituição Python (str.replace) na política
  python3 - "$POLICY" "$1" "$2" <<'PY'
import sys
path, old, new = sys.argv[1:4]
text = open(path).read()
assert old in text, f"trecho não encontrado: {old!r}"
open(path, "w").write(text.replace(old, new))
PY
}

echo "== Permissões por grupo (chave do portal)"
expect "Ana (DEVELOPER) → gemini"             allow "$(chat "$LITELLM_PORTAL_KEY" gemini "$ANA")"
expect "Bruno (FINANCE) → gemini"             deny "$(chat "$LITELLM_PORTAL_KEY" gemini "$BRUNO")"
expect "Bruno (FINANCE) → local-ai"           allow "$(chat "$LITELLM_PORTAL_KEY" local-ai "$BRUNO")"
expect "BRUNO em maiúsculas → gemini"         deny "$(chat "$LITELLM_PORTAL_KEY" gemini "${BRUNO^^}")"
expect "e-mail fora da política (USER) → local-ai" allow "$(chat "$LITELLM_PORTAL_KEY" local-ai "$UNKNOWN")"
expect "Ana → modelo inexistente na política" deny "$(chat "$LITELLM_PORTAL_KEY" gpt "$ANA")"

echo "== Listagem de modelos filtrada"
expect_list "Bruno (FINANCE) lista"            "auto local-ai"    "$(models "$LITELLM_PORTAL_KEY" "$BRUNO")"
expect_list "Ana (DEVELOPER) lista"            "auto gemini local-ai" "$(models "$LITELLM_PORTAL_KEY" "$ANA")"
expect_list "e-mail fora da política (USER) lista" "auto gemini local-ai" "$(models "$LITELLM_PORTAL_KEY" "$UNKNOWN")"
expect_list "chave do portal sem identidade lista" "(vazia)"     "$(models "$LITELLM_PORTAL_KEY")"
expect_list "chave mestra lista"               "auto gemini local-ai" "$(models "$LITELLM_MASTER_KEY")"
hidden=$(status_of "$LITELLM_PORTAL_KEY" /v1/models/gemini "$BRUNO")
missing=$(status_of "$LITELLM_PORTAL_KEY" /v1/models/modelo-que-nao-existe "$BRUNO")
[[ "$hidden" == "$missing" && "$hidden" != 200 ]] && ok "Bruno consulta /v1/models/gemini → $hidden, igual a modelo inexistente ($missing)" \
  || bad "Bruno /v1/models/gemini → $hidden, modelo inexistente → $missing (deveriam ser iguais e ≠ 200)"

echo "== Identidade e acesso administrativo"
expect "chave do portal sem e-mail → local-ai" deny "$(chat "$LITELLM_PORTAL_KEY" local-ai)"
expect "chave mestra sem e-mail → local-ai"    allow "$(chat "$LITELLM_MASTER_KEY" local-ai)"

echo "== Recarga sem reinício"
rewrite_policy "  FINANCE:
    models: [auto, local-ai]" "  FINANCE:
    models: [gemini]"
expect "FINANCE perde local-ai → Bruno local-ai" deny "$(chat "$LITELLM_PORTAL_KEY" local-ai "$BRUNO")"
expect_list "FINANCE ganha gemini → Bruno lista" "gemini" "$(models "$LITELLM_PORTAL_KEY" "$BRUNO")"

echo "== Fail-closed com política inválida"
printf 'version: 1\ngroups: [quebrado\n' > "$POLICY"
expect "política inválida → Ana local-ai"  deny "$(chat "$LITELLM_PORTAL_KEY" local-ai "$ANA")"
expect_list "política inválida → Ana lista"  "(vazia)" "$(models "$LITELLM_PORTAL_KEY" "$ANA")"
cp -p "$BACKUP" "$POLICY"; touch "$POLICY"
expect "política restaurada → Bruno local-ai" allow "$(chat "$LITELLM_PORTAL_KEY" local-ai "$BRUNO")"

echo "== Registro das negações (log do gateway)"
logs=$(docker compose logs --since "$SINCE" --no-log-prefix litellm 2>&1)
for reason in "user=$BRUNO model=gemini reason=model_not_allowed groups=FINANCE" \
              "user=- model=local-ai reason=missing_identity" \
              "user=$ANA model=local-ai reason=policy_unavailable"; do
  grep -qF "NEGADO $reason" <<<"$logs" && ok "log: NEGADO $reason" || bad "log sem: NEGADO $reason"
done
grep -qE "Bearer|sk-[A-Za-z0-9]{20,}" <<<"$(grep NEGADO <<<"$logs")" && bad "log de negação contém credencial" || ok "logs de negação sem credenciais"

echo
echo "Resultado: $PASS ok, $FAIL falha(s)"
[[ "$FAIL" -eq 0 ]]
