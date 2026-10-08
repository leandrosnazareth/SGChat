#!/usr/bin/env bash
# Testes de fumaça do Security Router (spec content-security), contra o gateway real.
# Cobre os testes 1, 2, 3, 5 e 10 do prompt.txt e o fail-closed. Para isso PARA e
# RELIGA temporariamente o presidio-analyzer e a local-ai (restaurados ao fim, mesmo
# em falha) e altera temporariamente litellm/policies/security.yaml.
# Só o caso PUBLIC chama o Gemini de verdade (1 requisição).
# Uso: tests/smoke/security.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
set -a; . ./.env; set +a

GW="http://127.0.0.1:${LITELLM_PORT:-4000}"
POLICY=litellm/policies/security.yaml
BACKUP="$(mktemp)"; cp -p "$POLICY" "$BACKUP"
TMP="$(mktemp)"
restore() {
  cp -p "$BACKUP" "$POLICY"; touch "$POLICY"
  docker compose start presidio-analyzer local-ai >/dev/null 2>&1
  rm -f "$BACKUP" "$TMP"
}
trap restore EXIT
RUN="sec-$(date +%s)"
PASS=0; FAIL=0
ok()  { echo "  ✔ $*"; PASS=$((PASS + 1)); }
bad() { echo "  ✘ $*"; FAIL=$((FAIL + 1)); }

chat() { # <e-mail> <modelo> <json das mensagens> → "<http_code> <error.message|->"
  : > "$TMP"
  local code
  code=$(curl -s -m 150 -o "$TMP" -w '%{http_code}' "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $LITELLM_PORTAL_KEY" -H 'Content-Type: application/json' \
    -H "x-litellm-end-user-id: $1" \
    -d "{\"model\":\"$2\",\"max_tokens\":20,\"messages\":$3}")
  echo "$code $(python3 -c "import json;print(json.load(open('$TMP')).get('error',{}).get('message','-'))" 2>/dev/null || echo -)"
}
msg() { python3 -c 'import json,sys;print(json.dumps([{"role":"user","content":sys.argv[1]}]))' "$1"; }
record() { # <e-mail> [status] → "model_group|classification|routing_reason|requested|effective|external_allowed|reasons"
  local status="${2:-success}" row=""
  for _ in $(seq 1 25); do
    row=$(docker compose exec -T postgres psql -U postgres -d litellm -tA -c "
      select model_group||'|'||coalesce(m->>'classification','')||'|'||coalesce(m->>'routing_reason','')||'|'||
             coalesce(m->>'requested_model','')||'|'||coalesce(m->>'effective_model','')||'|'||
             coalesce(m->>'external_allowed','')||'|'||coalesce(m->>'security_reasons','')
      from (select model_group, metadata::jsonb->'spend_logs_metadata' m, \"startTime\" from \"LiteLLM_SpendLogs\"
            where end_user='$1' and status='$status') s order by \"startTime\" desc limit 1")
    [[ -n "$row" ]] && break
    sleep 2
  done
  echo "$row"
}
expect_record() { # <descrição> <prefixo esperado> <obtido>
  [[ "$3" == "$2"* ]] && ok "$1 → ${3%%|\[*}" || bad "$1 → esperado [$2…], obtido [$3]"
}
wait_healthy() { # <serviço>
  for _ in $(seq 1 60); do
    [[ "$(docker inspect -f '{{.State.Health.Status}}' "sgchat-$1-1" 2>/dev/null)" == healthy ]] && return 0
    sleep 3
  done
  return 1
}

echo "== Teste 1 — conteúdo PUBLIC pode sair (1 chamada real ao gemini)"
U="$RUN-t1@smoke.local"
read -r code msg_ <<<"$(chat "$U" gemini "$(msg 'Explique Virtual Threads em Java.')")"
[[ "$code" == 200 ]] && ok "gemini → 200" || bad "gemini → HTTP $code ${msg_:0:80}"
expect_record "registro" "gemini|PUBLIC|DIRECT|gemini|gemini|true" "$(record "$U")"

echo "== Testes 2 e 5 — CONFIDENTIAL escolhendo gemini vai para a IA Local"
U="$RUN-t2@smoke.local"
read -r code _ <<<"$(chat "$U" gemini "$(msg 'Analise o contrato confidencial do cliente XPTO.')")"
[[ "$code" == 200 ]] && ok "gemini pedido → 200 (atendido)" || bad "HTTP $code"
expect_record "registro" "local-ai|CONFIDENTIAL|SECURITY_POLICY|gemini|local-ai|false" "$(record "$U")"

echo "== Teste 3 — RESTRICTED é bloqueado"
U="$RUN-t3@smoke.local"
for model in gemini local-ai; do
  read -r code m <<<"$(chat "$U" "$model" "$(msg 'Minha API Key é sk-xxxxxxxx.')")"
  [[ "$code" == 403 && "$m" == SECURITY_POLICY_BLOCKED ]] && ok "$model → 403 SECURITY_POLICY_BLOCKED" || bad "$model → $code $m"
done
logs=$(docker compose logs --since 2m --no-log-prefix litellm 2>&1)
grep -q "BLOQUEADO user=$U model=gemini classification=RESTRICTED reasons=regex:" <<<"$logs" && ok "log com classificação e regras" || bad "log de bloqueio ausente"
grep -q "sk-xxxxxxxx" <<<"$(grep security_router <<<"$logs")" && bad "log contém a chave" || ok "log não contém a chave"

echo "== Contexto completo e documentos"
U="$RUN-hist@smoke.local"
HIST='[{"role":"user","content":"O faturamento do cliente XPTO subiu 12%."},{"role":"assistant","content":"Entendido."},{"role":"user","content":"Resuma em uma frase."}]'
read -r code _ <<<"$(chat "$U" gemini "$HIST")"
expect_record "dado confidencial só no histórico" "local-ai|CONFIDENTIAL|SECURITY_POLICY|gemini|local-ai" "$(record "$U")"
U="$RUN-cpf@smoke.local"
read -r code _ <<<"$(chat "$U" gemini "$(msg 'Cadastre o CPF 529.982.247-25.')")"
expect_record "CPF válido" "local-ai|CONFIDENTIAL|SECURITY_POLICY|gemini|local-ai" "$(record "$U")"
U="$RUN-cpfx@smoke.local"
read -r code _ <<<"$(chat "$U" local-ai "$(msg 'O código 123.456.789-00 é só um exemplo.')")"
expect_record "CPF com dígito inválido não conta" "local-ai|PUBLIC|DIRECT|local-ai|local-ai" "$(record "$U")"

echo "== Fail-closed — Presidio parado"
docker compose stop presidio-analyzer >/dev/null 2>&1
U="$RUN-presidio@smoke.local"
read -r code _ <<<"$(chat "$U" gemini "$(msg 'Explique o que é um índice B-tree.')")"
[[ "$code" == 200 ]] && ok "gemini pedido → 200 (atendido localmente)" || bad "HTTP $code"
r=$(record "$U"); expect_record "registro" "local-ai|CONFIDENTIAL|SECURITY_POLICY|gemini|local-ai" "$r"
[[ "$r" == *detector_unavailable:presidio* ]] && ok "motivo detector_unavailable:presidio" || bad "motivo ausente: $r"
docker compose start presidio-analyzer >/dev/null 2>&1
wait_healthy presidio-analyzer && ok "presidio-analyzer religado (healthy)" || bad "presidio-analyzer não voltou"

echo "== Fail-closed — política de segurança inválida"
printf 'version: 1\nactions: [quebrado\n' > "$POLICY"
U="$RUN-policy@smoke.local"
read -r code _ <<<"$(chat "$U" gemini "$(msg 'Explique o que é um índice B-tree.')")"
r=$(record "$U"); expect_record "registro" "local-ai|CONFIDENTIAL|SECURITY_POLICY|gemini|local-ai" "$r"
[[ "$r" == *security_check_unavailable* ]] && ok "motivo security_check_unavailable" || bad "motivo ausente: $r"
cp -p "$BACKUP" "$POLICY"; touch "$POLICY"

echo "== Teste 10 — IA Local desligada não libera a nuvem"
docker compose stop local-ai >/dev/null 2>&1
U="$RUN-t10@smoke.local"
read -r code m <<<"$(chat "$U" gemini "$(msg 'Analise o contrato confidencial do cliente XPTO.')")"
[[ "$code" != 200 && "$code" != 000 ]] && ok "conteúdo confidencial com IA Local fora → erro HTTP $code" || bad "esperado erro, obtido $code"
sleep 12
external=$(docker compose exec -T postgres psql -U postgres -d litellm -tA -c \
  "select count(*) from \"LiteLLM_SpendLogs\" where end_user='$U' and model_group='gemini' and status='success'")
[[ "$external" == 0 ]] && ok "nenhuma chamada bem-sucedida ao gemini para esse usuário" || bad "$external chamada(s) ao gemini!"
docker compose start local-ai >/dev/null 2>&1
wait_healthy local-ai && ok "local-ai religada (healthy)" || bad "local-ai não voltou"

echo "== Política restaurada"
git diff --quiet -- "$POLICY" && ok "security.yaml igual ao versionado" || bad "security.yaml diferente do versionado"

echo
echo "Resultado: $PASS ok, $FAIL falha(s)"
[[ "$FAIL" -eq 0 ]]
