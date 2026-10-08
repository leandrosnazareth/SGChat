#!/usr/bin/env bash
# Testes de fumaça do registro de auditoria no Langfuse (spec audit-trail), contra o gateway
# e o Langfuse reais. Só usa a IA Local e bloqueios: nenhuma chamada ao Google.
# PARA e RELIGA o langfuse-web no último caso.
# Uso: tests/smoke/audit.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
set -a; . ./.env; set +a

GW="http://127.0.0.1:${LITELLM_PORT:-4000}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"; docker compose start langfuse-web >/dev/null 2>&1' EXIT
RUN="au-$(date +%s)"
U="$RUN@smoke.local"
CONV="conv-$RUN"
BRUNO=bruno.teste@corporate-ai.local
SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
PASS=0; FAIL=0
ok()  { echo "  ✔ $*"; PASS=$((PASS + 1)); }
bad() { echo "  ✘ $*"; FAIL=$((FAIL + 1)); }
check() { # <descrição> <expressão python sobre a observação "o" e seus metadados "m"> <arquivo json>
  if python3 -c "import json,sys;o=json.load(open('$3'));m=o.get('metadata') or {};sys.exit(0 if ($2) else 1)" 2>/dev/null; then
    ok "$1"; else bad "$1 — $(python3 -c "import json;o=json.load(open('$3'));print(json.dumps({k:o.get(k) for k in ('level','userId','sessionId','traceName','tags','metadata','input','totalUsage')},ensure_ascii=False)[:400])" 2>/dev/null)"; fi
}

chat() { # <e-mail> <corpo JSON> [cabeçalho extra] → código HTTP
  local extra=()
  [[ -n "${3:-}" ]] && extra=(-H "$3")
  : > "$TMP"
  curl -s -m 120 -o "$TMP" -w '%{http_code}' "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $LITELLM_PORTAL_KEY" -H 'Content-Type: application/json' \
    -H "x-litellm-end-user-id: $1" -H "x-litellm-session-id: $CONV" "${extra[@]}" -d "$2"
}
body() { # <modelo> <texto> [chaves extras JSON, sem chaves externas]
  python3 -c 'import json,sys;d={"model":sys.argv[1],"max_tokens":20,"messages":[{"role":"user","content":sys.argv[2]}]};d.update(json.loads(sys.argv[3]));print(json.dumps(d))' "$1" "$2" "${3:-{\}}"
}
# query <filtro JSON> → lista de observações (uma por trace) em JSON; aguarda a ingestão
query() {
  python3 - "$1" <<'PY'
import base64, json, os, sys, urllib.parse, urllib.request
auth = base64.b64encode(f"{os.environ['LANGFUSE_INIT_PROJECT_PUBLIC_KEY']}:{os.environ['LANGFUSE_INIT_PROJECT_SECRET_KEY']}".encode()).decode()
params = urllib.parse.urlencode({"fields": "core,basic,io,metadata,model,usage,metrics,trace_context", "limit": "100", "filter": sys.argv[1]})
req = urllib.request.Request(f"http://localhost:{os.environ.get('LANGFUSE_PORT','3000')}/api/public/v2/observations?{params}", headers={"Authorization": f"Basic {auth}"})
try:
    data = json.load(urllib.request.urlopen(req, timeout=20)).get("data", [])
except Exception as err:
    print("[]"); sys.exit(0)
seen, out = set(), []
for o in data:
    if o["traceId"] not in seen and o.get("type") == "GENERATION":
        seen.add(o["traceId"]); out.append(o)
print(json.dumps(out, ensure_ascii=False))
PY
}
f_user() { echo "{\"type\":\"string\",\"column\":\"userId\",\"operator\":\"=\",\"value\":\"$1\"}"; }
f_meta() { echo "{\"type\":\"stringObject\",\"column\":\"metadata\",\"key\":\"$1\",\"operator\":\"=\",\"value\":\"$2\"}"; }
f_text() { echo "{\"type\":\"string\",\"column\":\"input\",\"operator\":\"matches\",\"value\":\"$1\"}"; }
# trace <filtros JSON separados por vírgula> <arquivo> — espera um trace e grava a observação
trace() {
  local res="[]"
  for _ in $(seq 1 30); do
    res=$(query "[$1]")
    [[ "$res" != "[]" ]] && break
    sleep 2
  done
  python3 -c "import json,sys;d=json.loads(sys.argv[1]);json.dump(d[0] if d else {},open('$2','w'))" "$res"
  [[ "$res" != "[]" ]]
}
O="$(mktemp -d)"

echo "== Chamada atendida (IA Local)"
code=$(chat "$U" "$(body local-ai "Diga apenas: auditoria ok $RUN")")
[[ "$code" == 200 ]] || bad "chat → HTTP $code"
if trace "$(f_user "$U"),$(f_text "$RUN")" "$O/ok.json"; then
  check "usuário, conversa e nome do trace" "o['userId']=='$U' and o['sessionId']=='$CONV' and o['traceName']=='corporate-ai-chat'" "$O/ok.json"
  check "entrada e saída" "'auditoria ok $RUN' in o['input'] and len(o.get('output') or '')>10" "$O/ok.json"
  check "modelo, tokens, custo e latência" "'qwen' in o['model'] and o['totalUsage']>0 and o['totalCost']==0 and o['latency']>0" "$O/ok.json"
  check "decisão: PUBLIC, DIRECT, local-ai → local-ai" "m.get('classification')=='PUBLIC' and m.get('routing_reason')=='DIRECT' and m.get('requested_model')=='local-ai' and m.get('effective_model')=='local-ai'" "$O/ok.json"
  check "tags do gateway" "{'classification:PUBLIC','routing:DIRECT','requested:local-ai','effective:local-ai'} <= set(o['tags'])" "$O/ok.json"
else
  bad "trace da chamada atendida não chegou ao Langfuse"
fi

echo "== Confidencial redirecionado (gemini pedido, IA Local respondeu)"
code=$(chat "$U" "$(body gemini "O faturamento do cliente XPTO subiu 12%. Resuma. $RUN")")
[[ "$code" == 200 ]] || bad "chat → HTTP $code"
if trace "$(f_user "$U"),$(f_meta classification CONFIDENTIAL)" "$O/conf.json"; then
  check "classification, requested, effective, routing_reason, external_allowed" "m.get('requested_model')=='gemini' and m.get('effective_model')=='local-ai' and m.get('routing_reason')=='SECURITY_POLICY' and str(m.get('external_allowed')).lower()=='false'" "$O/conf.json"
  check "tags de redirecionamento" "{'classification:CONFIDENTIAL','requested:gemini','effective:local-ai','routing:SECURITY_POLICY'} <= set(o['tags'])" "$O/conf.json"
  check "conteúdo confidencial preservado para auditoria" "'XPTO' in o['input']" "$O/conf.json"
else
  bad "trace do redirecionamento não chegou"
fi

echo "== Senha bloqueada: segredo não vai para o Langfuse"
code=$(chat "$U" "$(body gemini "A senha do servidor de produção é Prod@2026! $RUN")")
[[ "$code" == 403 ]] && ok "bloqueio → 403" || bad "bloqueio → HTTP $code"
if trace "$(f_user "$U"),$(f_meta classification RESTRICTED)" "$O/rest.json"; then
  check "nível ERROR, blocked, sem effective_model, content_redacted" "o['level']=='ERROR' and str(m.get('blocked')).lower()=='true' and 'effective_model' not in m and str(m.get('content_redacted')).lower()=='true'" "$O/rest.json"
  check "marcador de redação na entrada" "'[REDACTED: RESTRICTED' in o['input'] and 'credencial-declarada' in o['input']" "$O/rest.json"
  if grep -q "Prod@2026" "$O/rest.json"; then bad "segredo presente no registro"; else ok "segredo ausente de todos os campos (input, parâmetros, metadados)"; fi
else
  bad "trace do bloqueio não chegou"
fi

echo "== Acesso negado pela política"
code=$(chat "$BRUNO" "$(body gemini "acesso negado $RUN")")
[[ "$code" == 403 ]] && ok "Bruno → gemini → 403" || bad "Bruno → HTTP $code"
if trace "$(f_user "$BRUNO"),$(f_text "$RUN")" "$O/acl.json"; then
  check "MODEL_ACCESS_DENIED, blocked, requested_model" "m.get('routing_reason')=='MODEL_ACCESS_DENIED' and m.get('access_reason')=='model_not_allowed' and m.get('requested_model')=='gemini' and str(m.get('blocked')).lower()=='true'" "$O/acl.json"
else
  bad "trace da negação não chegou"
fi

echo "== O cliente não controla o registro"
code=$(chat "$U" "$(body local-ai "mascarar $RUN" '{"metadata":{"mask_input":true,"tags":["cliente"],"trace_name":"cliente","trace_user_id":"outro@x","generation_name":"cliente"}}')" "x-litellm-enable-message-redaction: true")
[[ "$code" == 200 ]] || bad "chat → HTTP $code"
if trace "$(f_user "$U"),$(f_text "mascarar")" "$O/steer.json"; then
  check "entrada real, nome, usuário e tags do gateway" "'mascarar $RUN' in o['input'] and o['traceName']=='corporate-ai-chat' and o['userId']=='$U' and 'cliente' not in o['tags'] and 'routing:DIRECT' in o['tags']" "$O/steer.json"
else
  bad "trace com tentativa de mascarar não chegou (ou foi mascarado)"
fi
logs=$(docker compose logs --since 3m --no-log-prefix litellm 2>&1)   # captura antes do grep -q (pipefail/SIGPIPE)
grep "audit_metadata: chaves de registro" <<<"$logs" | grep -q "metadata.mask_input" \
  && ok "tentativa registrada no log do gateway" || bad "tentativa não registrada no log"

code=$(chat "$U" "$(body local-ai "desligar $RUN")" "x-litellm-disable-callbacks: langfuse")
[[ "$code" == 200 ]] || bad "chat → HTTP $code"
trace "$(f_user "$U"),$(f_text "desligar")" "$O/off.json" && ok "x-litellm-disable-callbacks ignorado: trace registrado" || bad "callback desligado pelo cliente"
code=$(chat "$U" "$(body local-ai "redirecionar $RUN" '{"langfuse_host":"http://example.com","langfuse_public_key":"pk-lf-x","langfuse_secret_key":"sk-lf-x"}')")
if [[ "$code" != 200 ]]; then
  ok "langfuse_host do cliente rejeitado pelo gateway (HTTP $code)"
else
  trace "$(f_user "$U"),$(f_text "redirecionar")" "$O/host.json" && ok "langfuse_host do cliente ignorado: trace no Langfuse interno" || bad "registro redirecionado pelo cliente"
fi

echo "== Busca (uma requisição para cada campo)"
count() { query "[$1]" | python3 -c "import json,sys;print(len(json.load(sys.stdin)))"; }
F_RUN="$(f_user "$U")"
[[ "$(count "$F_RUN")" -ge 5 ]] && ok "por usuário → $(count "$F_RUN") traces" || bad "por usuário → $(count "$F_RUN")"
for pair in "classification CONFIDENTIAL 1" "requested_model gemini 2" "effective_model local-ai 4" "routing_reason SECURITY_POLICY 2" "blocked true 1"; do
  set -- $pair
  n=$(count "$F_RUN,$(f_meta "$1" "$2")")
  [[ "$n" == "$3" ]] && ok "usuário + $1=$2 → $n" || bad "usuário + $1=$2 → esperado $3, obtido $n"
done
n=$(scripts/audit-search.sh --user "$U" --from "${SINCE:0:10}" --reason SECURITY_POLICY --show | grep -c "pergunta:")
[[ "$n" == 2 ]] && ok "scripts/audit-search.sh --user --from --reason --show → $n chamadas" || bad "audit-search → $n"

echo "== Langfuse fora do ar não afeta o atendimento"
docker compose stop langfuse-web >/dev/null 2>&1
start=$(date +%s%N)
code=$(chat "$U" "$(body local-ai "sem langfuse $RUN")")
ms=$(( ($(date +%s%N) - start) / 1000000 ))
[[ "$code" == 200 && "$ms" -lt 10000 ]] && ok "chat com Langfuse parado → 200 em ${ms} ms" || bad "chat com Langfuse parado → HTTP $code em ${ms} ms"
sleep 3
logs=$(docker compose logs --since 1m --no-log-prefix litellm 2>&1)
grep -q "Langfuse export" <<<"$logs" \
  && ok "falha de envio registrada no log do gateway" || bad "falha de envio não registrada"
docker compose start langfuse-web >/dev/null 2>&1
for _ in $(seq 1 40); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' sgchat-langfuse-web-1 2>/dev/null)" == healthy ]] && break
  sleep 3
done
[[ "$(docker inspect -f '{{.State.Health.Status}}' sgchat-langfuse-web-1)" == healthy ]] && ok "langfuse-web religado (healthy)" || bad "langfuse-web não voltou"
rm -rf "$O"

echo
echo "Resultado: $PASS ok, $FAIL falha(s)"
[[ "$FAIL" -eq 0 ]]
