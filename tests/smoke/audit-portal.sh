#!/usr/bin/env bash
# Testes de fumaça do portal de auditoria e da trilha de acesso (spec auditor-access), contra a
# stack real. Cria uma conversa pela IA Local (nenhuma chamada ao Google) e a audita.
# Usa 4 logins (o LibreChat limita 7 tentativas por IP em 5 minutos).
# Revoga e devolve temporariamente o EXECUTE do portal na função da trilha (caso fail-closed).
# Uso: tests/smoke/audit-portal.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
set -a; . ./.env; set +a

GW="http://127.0.0.1:${LITELLM_PORT:-4000}"
AP="http://localhost:${AUDIT_PORTAL_PORT:-3090}"
RUN="ap-$(date +%s)"
U="$RUN@smoke.local"
CONV="conv-$RUN"
TMP="$(mktemp -d)"
GRANT="GRANT EXECUTE ON FUNCTION audit.append_access_event(text, text, text, text[], text, text[], text, text, jsonb, text, integer, text) TO audit_portal"
trap 'docker compose exec -T postgres psql -U postgres -d audit -qc "$GRANT" >/dev/null 2>&1; rm -rf "$TMP"' EXIT
PASS=0; FAIL=0
ok()  { echo "  ✔ $*"; PASS=$((PASS + 1)); }
bad() { echo "  ✘ $*"; FAIL=$((FAIL + 1)); }
expect() { [[ "$3" == "$2" ]] && ok "$1 → $3" || bad "$1 → esperado [$2], obtido [$3]"; }
pw() { awk -v u="$1" '$1==u{print $2}' .test-users; }
sql() { docker compose exec -T postgres psql -U postgres -d audit -tA -F '|' -c "$1" 2>&1 || true; }
# api <jar> <método> <caminho> [json] → código HTTP; corpo em $TMP/body, cabeçalhos em $TMP/headers
api() {
  local args=(-s -m 60 -o "$TMP/body" -D "$TMP/headers" -w '%{http_code}' -b "$TMP/$1" -c "$TMP/$1" -X "$2" "$AP$3")
  [[ -n "${4:-}" ]] && args+=(-H 'Content-Type: application/json' -d "$4")
  curl "${args[@]}"
}
login() { api "$1" POST /api/login "{\"email\":\"$2\",\"password\":\"$3\"}"; }
# último evento após START: "action|outcome|actor|roles|channel|targets|conversation|trace|reason|count|query"
last_event() { sql "SELECT action, outcome, actor, array_to_string(actor_roles, ','), channel, array_to_string(target_users, ','), coalesce(conversation_id, ''), coalesce(trace_id, ''), coalesce(reason, ''), coalesce(result_count::text, ''), query::text FROM audit.access_log WHERE id > $START ORDER BY id DESC LIMIT 1"; }
field() { cut -d'|' -f"$1" <<<"$2"; }

for f in carla.auditora diego.master edu.admin; do
  [[ -n "$(pw "$f@corporate-ai.local")" ]] || { echo "usuário $f ausente em .test-users (docs/audit.md)"; exit 2; }
done
START=$(sql "SELECT coalesce(max(id), 0) FROM audit.access_log")

echo "== Preparação: conversa pela IA Local"
code=$(curl -s -m 120 -o /dev/null -w '%{http_code}' "$GW/v1/chat/completions" -H "Authorization: Bearer $LITELLM_PORTAL_KEY" \
  -H 'Content-Type: application/json' -H "x-litellm-end-user-id: $U" -H "x-litellm-session-id: $CONV" \
  -d "{\"model\":\"local-ai\",\"max_tokens\":15,\"messages\":[{\"role\":\"user\",\"content\":\"Diga ok. marcador-$RUN\"}]}")
[[ "$code" == 200 ]] && ok "conversa criada ($CONV)" || bad "chat → HTTP $code"
filter="[{\"type\":\"string\",\"column\":\"sessionId\",\"operator\":\"=\",\"value\":\"$CONV\"}]"
for _ in $(seq 1 30); do
  n=$(curl -s -u "$LANGFUSE_INIT_PROJECT_PUBLIC_KEY:$LANGFUSE_INIT_PROJECT_SECRET_KEY" -G "http://localhost:${LANGFUSE_PORT:-3000}/api/public/v2/observations" \
      --data-urlencode "filter=$filter" | python3 -c "import json,sys;print(len(json.load(sys.stdin).get('data',[])))" 2>/dev/null)
  [[ "${n:-0}" -gt 0 ]] && break; sleep 2
done
[[ "${n:-0}" -gt 0 ]] && ok "conversa registrada no Langfuse" || bad "conversa não chegou ao Langfuse"

echo "== Sem sessão, senha errada e administrador"
expect "busca sem sessão" 401 "$(api anon GET "/api/search?user=$U")"
expect "login com senha errada" 401 "$(login anon diego.master@corporate-ai.local senha-errada-123)"
e=$(last_event); expect "evento" "LOGIN|FAILED|diego.master@corporate-ai.local" "$(field 1-3 "$e")"
expect "login do ADMIN (edu)" 403 "$(login edu edu.admin@corporate-ai.local "$(pw edu.admin@corporate-ai.local)")"
e=$(last_event); expect "evento" "LOGIN|DENIED|edu.admin@corporate-ai.local" "$(field 1-3 "$e")"

echo "== AUDITOR (carla): só metadados"
expect "login" 200 "$(login carla carla.auditora@corporate-ai.local "$(pw carla.auditora@corporate-ai.local)")"
expect "busca por usuário" 200 "$(api carla GET "/api/search?user=$U")"
python3 -c "import json;d=json.load(open('$TMP/body'));r=d['results'][0];assert d['count']==1 and r['conversation_id']=='$CONV' and r['effective_model']=='local-ai'" 2>/dev/null \
  && ok "1 chamada com metadados (conversa, modelo)" || bad "resultado da busca: $(head -c 300 "$TMP/body")"
grep -q "marcador-$RUN" "$TMP/body" && bad "conteúdo vazou para o AUDITOR" || ok "nenhum conteúdo na resposta do AUDITOR"
e=$(last_event); expect "evento" "SEARCH_CONVERSATIONS|ALLOWED|carla.auditora@corporate-ai.local|AUDITOR|portal|$U" "$(field 1-6 "$e")"
expect "quantidade registrada" 1 "$(field 10 "$e")"
expect "busca por termo (revela conteúdo)" 403 "$(api carla GET "/api/search?text=marcador-$RUN")"
expect "abrir conversa" 403 "$(api carla POST "/api/conversations/$CONV" '{"reason":"Investigação do chamado 42"}')"
e=$(last_event); expect "evento" "VIEW_CONVERSATION|DENIED|carla.auditora@corporate-ai.local" "$(field 1-3 "$e")"
expect "exportar conversa" 403 "$(api carla POST "/api/conversations/$CONV/export" '{"reason":"Investigação do chamado 42"}')"
expect "trilha de acesso" 403 "$(api carla GET /api/access-log)"
expect "negações registradas" 4 "$(sql "SELECT count(*) FROM audit.access_log WHERE id > $START AND actor = 'carla.auditora@corporate-ai.local' AND outcome = 'DENIED'")"

echo "== MASTER_AUDITOR (diego): conteúdo com motivo"
expect "login" 200 "$(login diego diego.master@corporate-ai.local "$(pw diego.master@corporate-ai.local)")"
expect "abrir sem motivo" 400 "$(api diego POST "/api/conversations/$CONV" '{}')"
grep -q "marcador-$RUN" "$TMP/body" && bad "conteúdo entregue sem motivo" || ok "sem motivo, sem conteúdo"
expect "abrir com motivo" 200 "$(api diego POST "/api/conversations/$CONV" '{"reason":"Investigação do chamado 42"}')"
grep -q "marcador-$RUN" "$TMP/body" && ok "pergunta da conversa exibida" || bad "conteúdo ausente: $(head -c 200 "$TMP/body")"
e=$(last_event)
expect "evento" "VIEW_CONVERSATION|ALLOWED|diego.master@corporate-ai.local|MASTER_AUDITOR|portal|$U|$CONV||Investigação do chamado 42|1" "$(field 1-10 "$e")"
expect "exportar" 200 "$(api diego POST "/api/conversations/$CONV/export" '{"reason":"Pedido do jurídico, processo 7"}')"
grep -qi 'content-disposition: attachment' "$TMP/headers" && ok "exportação como anexo" || bad "sem Content-Disposition"
event_id=$(python3 -c "import json;print(json.load(open('$TMP/body'))['access_event_id'])" 2>/dev/null)
e=$(last_event); expect "evento" "EXPORT_CONVERSATION|ALLOWED|diego.master@corporate-ai.local" "$(field 1-3 "$e")"
expect "access_event_id no arquivo = evento gravado" "$(sql "SELECT max(id) FROM audit.access_log WHERE action = 'EXPORT_CONVERSATION'")" "$event_id"
expect "trilha de acesso" 200 "$(api diego GET /api/access-log)"
python3 -c "import json;assert json.load(open('$TMP/body'))['chain']['intact'] is True" 2>/dev/null && ok "cadeia íntegra segundo o portal" || bad "cadeia: $(head -c 200 "$TMP/body")"
e=$(last_event); expect "evento" "VIEW_ACCESS_LOG|ALLOWED" "$(field 1-2 "$e")"
expect "página de busca (HTML)" 200 "$(api diego GET "/?run=1&user=$U")"
grep -qi "cache-control: no-store" "$TMP/headers" && grep -qi "content-security-policy: default-src 'none'" "$TMP/headers" \
  && ok "cabeçalhos no-store e CSP" || bad "cabeçalhos de segurança ausentes"

echo "== Trilha imutável"
# psql sai com 1 no erro esperado: captura antes do grep (com pipefail, o pipe falharia)
portal_sql() { docker compose exec -T -e PGPASSWORD="$AUDIT_DB_PASSWORD" postgres psql -h localhost -U audit_portal -d audit -tA -c "$1" 2>&1 || true; }
portal_sql "UPDATE audit.access_log SET reason = 'x' WHERE id = $START + 1" | grep -q "permission denied" && ok "portal: UPDATE negado" || bad "portal conseguiu UPDATE"
portal_sql "DELETE FROM audit.access_log" | grep -q "permission denied" && ok "portal: DELETE negado" || bad "portal conseguiu DELETE"
portal_sql "INSERT INTO audit.access_log (id, occurred_at, action, outcome, actor, channel, prev_hash, hash) VALUES (-1, now(), 'LOGIN', 'ALLOWED', 'x', 'portal', '0', 'x')" \
  | grep -q "permission denied" && ok "portal: INSERT direto negado (só pela função)" || bad "portal inseriu fora da função"
sql "UPDATE audit.access_log SET reason = 'x' WHERE id = $START + 1" | grep -q "somente inclusão" && ok "superusuário: UPDATE barrado pelo trigger" || bad "UPDATE do superusuário passou"
sql "DELETE FROM audit.access_log WHERE id = $START + 1" | grep -q "somente inclusão" && ok "superusuário: DELETE barrado pelo trigger" || bad "DELETE do superusuário passou"
tamper=$(docker compose exec -T postgres psql -U postgres -d audit -tA -F '|' <<SQL 2>&1
BEGIN;
ALTER TABLE audit.access_log DISABLE TRIGGER access_log_no_update;
UPDATE audit.access_log SET reason = 'adulterado' WHERE id = $START + 1;
SELECT first_invalid_id FROM audit.verify_access_chain();
ROLLBACK;
SQL
)
grep -qx "$((START + 1))" <<<"$tamper" && ok "adulteração detectada no evento #$((START + 1)) (transação revertida)" || bad "adulteração não detectada: $tamper"

echo "== Fail-closed: sem trilha, sem conteúdo"
sql "REVOKE EXECUTE ON FUNCTION audit.append_access_event(text, text, text, text[], text, text[], text, text, jsonb, text, integer, text) FROM audit_portal" >/dev/null
expect "abrir conversa com a trilha indisponível" 503 "$(api diego POST "/api/conversations/$CONV" '{"reason":"Investigação do chamado 42"}')"
grep -q "marcador-$RUN" "$TMP/body" && bad "conteúdo entregue sem trilha" || ok "nenhum conteúdo entregue"
expect "busca com a trilha indisponível" 503 "$(api diego GET "/api/search?user=$U")"
sql "$GRANT" >/dev/null
expect "trilha de volta: abrir conversa" 200 "$(api diego POST "/api/conversations/$CONV" '{"reason":"Investigação do chamado 42"}')"

echo "== Operação (CLI) também auditada e verificação"
scripts/audit-search.sh --user "$U" --limit 1 >/dev/null 2>&1
e=$(last_event); expect "evento do CLI" "SEARCH_CONVERSATIONS|ALLOWED" "$(field 1-2 "$e")"
expect "canal e perfil" "OPERATOR|cli|$U" "$(field 4-6 "$e")"
expect "logout" 200 "$(api diego POST /api/logout)"
expect "sessão encerrada" 401 "$(api diego GET "/api/search?user=$U")"
scripts/audit-access-verify.sh >/dev/null 2>&1 && ok "scripts/audit-access-verify.sh: cadeia íntegra" || bad "verificação da cadeia falhou"

echo
echo "Resultado: $PASS ok, $FAIL falha(s)"
[[ "$FAIL" -eq 0 ]]
