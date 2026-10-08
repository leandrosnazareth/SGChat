#!/usr/bin/env bash
# Testes de fumaça do AI Gateway (LiteLLM), executados a partir do host contra
# 127.0.0.1:${LITELLM_PORT}. Cobre a spec ai-gateway. A identidade do usuário final
# é um e-mail fora da política de acesso (grupo padrão USER), como o portal envia.
# Economia de cota do Gemini (plano gratuito: 20 req/dia por modelo): por padrão
# faz UMA chamada real ao gemini (streaming com uso de tokens). Para incluir
# também o chat sem streaming: GATEWAY_SMOKE_FULL=1 tests/smoke/gateway.sh
# Uso: tests/smoke/gateway.sh
set -uo pipefail
export LC_NUMERIC=C
cd "$(dirname "$0")/../.."

set -a; . ./.env; set +a
GW="http://127.0.0.1:${LITELLM_PORT:-4000}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"; docker rm -f sgchat-litellm-badkey >/dev/null 2>&1' EXIT
PASS=0; FAIL=0; SKIP=0
ok()   { echo "  ✔ $*"; PASS=$((PASS + 1)); }
bad()  { echo "  ✘ $*"; FAIL=$((FAIL + 1)); }
skip() { echo "  ⚠ $* (pulado)"; SKIP=$((SKIP + 1)); }
json() { python3 -c "import json,sys; d=json.load(open('$1')); print($2)" 2>/dev/null; }

RUN_ID="smoke-$(date +%s)"
END_USER="$RUN_ID@smoke.local"   # e-mail fora da política → grupo padrão (USER)
chat_body() { # modelo, stream (com stream, pede o uso de tokens no último evento)
  local opts=""
  [[ "$2" == true ]] && opts=',"stream_options":{"include_usage":true}'
  printf '{"model":"%s","stream":%s%s,"max_tokens":40,"temperature":0,"messages":[{"role":"user","content":"Responda apenas com a palavra: pronto"}]}' "$1" "$2" "$opts"
}
post_chat() { # chave, modelo, stream, saída -> imprime HTTP status
  curl -sS -m 120 -o "$4" -w '%{http_code}' "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $1" -H 'Content-Type: application/json' \
    -H "x-litellm-end-user-id: $END_USER" -H "x-litellm-session-id: $RUN_ID-session" \
    -d "$(chat_body "$2" "$3")"
}

echo "== Autenticação"
code=$(curl -s -o /dev/null -w '%{http_code}' "$GW/v1/models")
[[ "$code" == 401 ]] && ok "sem chave → 401" || bad "sem chave → esperado 401, obtido $code"
code=$(curl -s -o /dev/null -w '%{http_code}' "$GW/v1/models" -H 'Authorization: Bearer sk-chave-invalida-123456')
[[ "$code" == 401 ]] && ok "chave inválida → 401" || bad "chave inválida → esperado 401, obtido $code"
code=$(curl -s -o /dev/null -w '%{http_code}' "$GW/v1/chat/completions" -H 'Content-Type: application/json' -d "$(chat_body local-ai false)")
[[ "$code" == 401 ]] && ok "chat sem chave → 401 (nada encaminhado)" || bad "chat sem chave → esperado 401, obtido $code"

echo "== Catálogo (chave do portal)"
code=$(curl -s -o "$TMP/models.json" -w '%{http_code}' "$GW/v1/models" -H "Authorization: Bearer $LITELLM_PORTAL_KEY" -H "x-litellm-end-user-id: $END_USER")
models=$(json "$TMP/models.json" "' '.join(sorted(m['id'] for m in d['data']))")
[[ "$code" == 200 && "$models" == "auto gemini local-ai" ]] && ok "/v1/models → [$models]" || bad "/v1/models → HTTP $code [$models]"

echo "== local-ai"
code=$(post_chat "$LITELLM_PORTAL_KEY" local-ai false "$TMP/local.json")
usage=$(json "$TMP/local.json" "'%d %d %d' % (d['usage']['prompt_tokens'], d['usage']['completion_tokens'], d['usage']['total_tokens'])")
if [[ "$code" == 200 && -n "$usage" ]] && read -r p c t <<<"$usage" && (( p > 0 && c > 0 && t > 0 )); then
  ok "chat → 200, usage prompt=$p completion=$c total=$t, resposta: $(json "$TMP/local.json" "d['choices'][0]['message']['content'].strip()[:40]")"
else bad "chat → HTTP $code, usage='$usage'"; fi
code=$(post_chat "$LITELLM_PORTAL_KEY" local-ai true "$TMP/local.sse")
chunks=$(grep -c '^data: {' "$TMP/local.sse"); last=$(grep '^data:' "$TMP/local.sse" | tail -1)
[[ "$code" == 200 && "$chunks" -gt 1 && "$last" == "data: [DONE]" ]] && ok "streaming → $chunks eventos SSE, termina em [DONE]" || bad "streaming → HTTP $code, $chunks eventos, último='$last'"

echo "== gemini"
if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  skip "GEMINI_API_KEY vazia"
else
  if [[ "${GATEWAY_SMOKE_FULL:-0}" == 1 ]]; then
    code=$(post_chat "$LITELLM_PORTAL_KEY" gemini false "$TMP/gemini.json")
    usage=$(json "$TMP/gemini.json" "d['usage']['total_tokens']")
    [[ "$code" == 200 && "${usage:-0}" -gt 0 ]] && ok "chat → 200, total_tokens=$usage, modelo=$(json "$TMP/gemini.json" "d['model']")" || bad "chat → HTTP $code: $(head -c 300 "$TMP/gemini.json")"
  fi
  code=$(post_chat "$LITELLM_PORTAL_KEY" gemini true "$TMP/gemini.sse")
  chunks=$(grep -c '^data: {' "$TMP/gemini.sse"); last=$(grep '^data:' "$TMP/gemini.sse" | tail -1)
  usage=$(grep '^data: {' "$TMP/gemini.sse" | sed 's/^data: //' | python3 -c "
import json,sys
t=[json.loads(l).get('usage') or {} for l in sys.stdin if l.strip()]
print(max([u.get('total_tokens',0) for u in t] or [0]))" 2>/dev/null)
  [[ "$code" == 200 && "$chunks" -ge 1 && "$last" == "data: [DONE]" && "${usage:-0}" -gt 0 ]] \
    && ok "streaming → 200, $chunks eventos SSE, termina em [DONE], total_tokens=$usage" \
    || bad "streaming → HTTP $code, $chunks eventos, último='$last', total_tokens=${usage:-?}: $(head -c 200 "$TMP/gemini.sse")"
fi

echo "== gemini com chave inválida (instância temporária do gateway)"
docker compose run -d --rm --no-deps --name sgchat-litellm-badkey \
  -e GEMINI_API_KEY=chave-gemini-invalida litellm >/dev/null 2>&1
for _ in $(seq 1 60); do
  docker compose exec -T litellm python3 -c "import urllib.request;urllib.request.urlopen('http://sgchat-litellm-badkey:4000/health/liveliness',timeout=3)" >/dev/null 2>&1 && break
  sleep 2
done
result=$(docker compose exec -T -e BODY="$(chat_body gemini false)" litellm python3 - <<'PY'
import json, os, urllib.request, urllib.error
req = urllib.request.Request("http://sgchat-litellm-badkey:4000/v1/chat/completions",
    data=os.environ["BODY"].encode(),
    headers={"Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"], "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        print(r.status, json.loads(r.read()).get("model", "?"))
except urllib.error.HTTPError as e:
    body = json.loads(e.read() or b"{}")
    print(e.code, "error" in body)
PY
)
read -r code detail <<<"$result"
[[ "$code" =~ ^[45][0-9][0-9]$ && "$detail" == True ]] && ok "chave Gemini inválida → HTTP $code com erro no formato OpenAI (sem fallback)" || bad "chave Gemini inválida → esperado erro, obtido '$result'"
docker rm -f sgchat-litellm-badkey >/dev/null 2>&1

echo "== Uso persistido (spend logs)"
rows=""
for _ in $(seq 1 30); do
  rows=$(docker compose exec -T postgres psql -U postgres -d litellm -tAF '|' -c \
    "select model_group, end_user, session_id, total_tokens, spend from \"LiteLLM_SpendLogs\" where end_user = '$END_USER' and status = 'success' order by \"startTime\"")
  [[ $(grep -c . <<<"$rows") -ge 2 ]] && break
  sleep 3
done
local_row=$(grep '^local-ai|' <<<"$rows" | head -1)
if [[ -n "$local_row" ]]; then
  IFS='|' read -r m u s t sp <<<"$local_row"
  [[ "$u" == "$END_USER" && "$s" == "$RUN_ID-session" && "$t" -gt 0 && "$sp" == 0 ]] \
    && ok "local-ai registrado: end_user=$u session=$s tokens=$t custo=$sp" \
    || bad "local-ai registrado com dados inesperados: $local_row"
else bad "nenhum registro de uso de local-ai para $END_USER"; fi
if [[ -n "${GEMINI_API_KEY:-}" ]]; then
  gem_row=$(grep '^gemini|' <<<"$rows" | head -1)
  [[ -n "$gem_row" ]] && ok "gemini registrado: $(cut -d'|' -f2,4,5 <<<"$gem_row" | sed 's/|/ tokens=/; s/|/ custo=/')" || bad "nenhum registro de uso de gemini"
fi

echo
echo "Resultado: $PASS ok, $FAIL falha(s), $SKIP pulado(s)"
[[ "$FAIL" -eq 0 ]]
