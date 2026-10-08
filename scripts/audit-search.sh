#!/usr/bin/env bash
# Busca no registro de auditoria (Langfuse) as chamadas ao AI Gateway.
# Uma linha por chamada (trace); --show imprime também a entrada e a saída.
#
# Uso: scripts/audit-search.sh [filtros] [--show] [--json] [--limit N]
#   --user EMAIL            usuário (e-mail do portal)
#   --conversation ID       conversa (ID da conversa do LibreChat = sessão no Langfuse), em ordem cronológica
#   --from DATA --to DATA   período (ISO 8601, ex.: 2026-10-07 ou 2026-10-07T14:00:00Z)
#   --classification NÍVEL  PUBLIC | INTERNAL | CONFIDENTIAL | RESTRICTED
#   --requested MODELO      modelo pedido (ex.: gemini, auto)
#   --effective MODELO      modelo que respondeu (ex.: local-ai)
#   --reason MOTIVO         routing_reason (DIRECT, AUTO, SECURITY_POLICY, TOKEN_QUOTA_EXCEEDED,
#                           BUDGET_EXCEEDED, QUOTA_CHECK_UNAVAILABLE, MODEL_ACCESS_DENIED)
#   --blocked               só requisições bloqueadas
#   --errors                só chamadas com erro (nível ERROR: bloqueios e falhas de provedor)
#   --external              só chamadas atendidas por modelo externo (não local-ai)
#   --text TEXTO            mensagens que contêm o termo (entrada)
#   --reason-text TEXTO     motivo da consulta (gravado na trilha; recomendado com --show)
#
# Quem audita também é auditado (Fase 9): antes de consultar, a busca é gravada na trilha de
# acesso (banco audit) como SEARCH_CONVERSATIONS — ou VIEW_CONVERSATION com --show — com
# channel=cli, o usuário do sistema operacional e os filtros. Sem gravar, não consulta.
# Auditores usam o portal de auditoria (http://localhost:3090); este script é de operação.
# Guia: docs/audit.md
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

exec python3 - "$@" <<'PY'
import base64, getpass, json, os, socket, subprocess, sys, urllib.error, urllib.parse, urllib.request

LOCAL_MODELS = {"local-ai"}
args, filters, opts = sys.argv[1:], [], {"show": False, "json": False, "limit": 50, "external": False}
logged = {}  # opções como digitadas, para a trilha

def meta(key, value):
    filters.append({"type": "stringObject", "column": "metadata", "key": key, "operator": "=", "value": value})

def day(value, end=False):
    if len(value) == 10:
        value += "T23:59:59.999Z" if end else "T00:00:00Z"
    return value

i = 0
while i < len(args):
    a = args[i]
    nxt = lambda: args[i + 1] if i + 1 < len(args) else sys.exit(f"faltou o valor de {a}")
    if a in ("-h", "--help"):
        print(open("scripts/audit-search.sh").read().split("set -euo")[0].replace("# ", "").replace("#", "")); sys.exit(0)
    elif a == "--user": filters.append({"type": "string", "column": "userId", "operator": "=", "value": nxt()}); i += 1
    elif a == "--conversation": filters.append({"type": "string", "column": "sessionId", "operator": "=", "value": nxt()}); opts["chronological"] = True; i += 1
    elif a == "--from": filters.append({"type": "datetime", "column": "startTime", "operator": ">=", "value": day(nxt())}); i += 1
    elif a == "--to": filters.append({"type": "datetime", "column": "startTime", "operator": "<=", "value": day(nxt(), True)}); i += 1
    elif a == "--classification": meta("classification", nxt().upper()); i += 1
    elif a == "--requested": meta("requested_model", nxt()); i += 1
    elif a == "--effective": meta("effective_model", nxt()); i += 1
    elif a == "--reason": meta("routing_reason", nxt().upper()); i += 1
    elif a == "--blocked": meta("blocked", "true")
    elif a == "--errors": filters.append({"type": "string", "column": "level", "operator": "=", "value": "ERROR"})
    elif a == "--external": opts["external"] = True
    elif a == "--text": filters.append({"type": "string", "column": "input", "operator": "matches", "value": nxt()}); i += 1
    elif a == "--show": opts["show"] = True
    elif a == "--json": opts["json"] = True
    elif a == "--limit": opts["limit"] = int(nxt()); i += 1
    elif a == "--reason-text": opts["reason"] = nxt(); i += 1
    else: sys.exit(f"opção desconhecida: {a} (use --help)")
    if a not in ("--show", "--json", "--limit", "--reason-text", "-h", "--help"):
        logged[a.lstrip("-")] = args[i] if a not in ("--blocked", "--errors", "--external") else True
    i += 1

def record_access():
    """Grava a consulta na trilha de acesso; sem trilha, não consulta (fail-closed)."""
    action = "VIEW_CONVERSATION" if opts["show"] else "SEARCH_CONVERSATIONS"
    sql = ("SELECT event_id FROM audit.append_access_event(:'action', 'ALLOWED', :'actor', '{OPERATOR}', 'cli', "
           "ARRAY(SELECT jsonb_array_elements_text(:'targets'::jsonb)), NULLIF(:'conv', ''), NULL, :'query'::jsonb, "
           "NULLIF(:'reason', ''), NULL, NULL)")
    cmd = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "audit", "-tA",
           "-v", "ON_ERROR_STOP=1", "-v", f"action={action}", "-v", f"actor={getpass.getuser()}@{socket.gethostname()}",
           "-v", "targets=" + json.dumps([logged["user"]] if "user" in logged else []),
           "-v", f"conv={logged.get('conversation', '')}", "-v", "query=" + json.dumps(logged, ensure_ascii=False),
           "-v", f"reason={opts.get('reason', '')}"]
    try:
        out = subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as err:
        sys.exit(f"trilha de acesso indisponível ({err}); consulta não realizada")
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit(f"trilha de acesso indisponível ({out.stderr.strip()[:200]}); consulta não realizada")
    print(f"(consulta registrada na trilha de acesso: evento #{out.stdout.strip()}, {action})", file=sys.stderr)

record_access()

host = f"http://localhost:{os.environ.get('LANGFUSE_PORT', '3000')}"
auth = base64.b64encode(f"{os.environ['LANGFUSE_INIT_PROJECT_PUBLIC_KEY']}:{os.environ['LANGFUSE_INIT_PROJECT_SECRET_KEY']}".encode()).decode()
params = {"fields": "core,basic,io,metadata,model,usage,metrics,trace_context",
          "limit": str(min(1000, opts["limit"] * 2)), "expandMetadata": "auto_decision,security_reasons"}
if filters:
    params["filter"] = json.dumps(filters)
request = urllib.request.Request(f"{host}/api/public/v2/observations?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"Basic {auth}"})
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response).get("data", [])
except urllib.error.HTTPError as err:
    sys.exit(f"Langfuse respondeu {err.code}: {err.read().decode()[:300]}")
except OSError as err:
    sys.exit(f"Langfuse indisponível em {host}: {err}")

# O LiteLLM registra falhas geradas no próprio gateway duas vezes no mesmo trace: uma linha por trace.
rows, seen = [], set()
for obs in data:
    if obs["traceId"] in seen or obs.get("type") != "GENERATION":
        continue
    m = obs.get("metadata") or {}
    effective = m.get("effective_model") or ""
    if opts["external"] and (not effective or effective in LOCAL_MODELS):
        continue
    seen.add(obs["traceId"])
    rows.append({
        "time": obs["startTime"][:19].replace("T", " "), "user": obs.get("userId") or "-",
        "conversation": obs.get("sessionId") or "-", "requested": m.get("requested_model") or "-",
        "effective": effective or "-", "classification": m.get("classification") or "-",
        "reason": m.get("routing_reason") or "-", "tokens": obs.get("totalUsage") or 0,
        "cost": obs.get("totalCost") or 0, "status": "BLOQUEADO" if str(m.get("blocked")).lower() == "true"
        else ("ERRO" if obs.get("level") == "ERROR" else "ok"), "trace": obs["traceId"],
        "input": obs.get("input"), "output": obs.get("output"), "error": obs.get("statusMessage") or "",
    })
    if len(rows) >= opts["limit"]:
        break

if opts.get("chronological"):
    rows.reverse()  # conversa: na ordem em que aconteceu

if opts["json"]:
    print(json.dumps(rows, ensure_ascii=False, indent=2)); sys.exit(0)

def last_user_message(raw):
    try:
        messages = json.loads(raw).get("messages") or []
        content = [m for m in messages if m.get("role") == "user"][-1]["content"]
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    except Exception:
        return raw or ""

def answer(raw):
    try:
        return json.loads(raw).get("content") or raw
    except Exception:
        return raw or ""

header = f"{'HORA (UTC)':19}  {'USUÁRIO':30}  {'CONVERSA':36}  {'PEDIDO → EFETIVO':22}  {'CLASSIF.':12}  {'MOTIVO':22}  {'TOKENS':>6}  {'CUSTO US$':>10}  STATUS"
print(header); print("-" * len(header))
for r in rows:
    print(f"{r['time']:19}  {r['user'][:30]:30}  {r['conversation'][:36]:36}  {(r['requested'] + ' → ' + r['effective'])[:22]:22}  "
          f"{r['classification']:12}  {r['reason'][:22]:22}  {r['tokens']:>6}  {r['cost']:>10.6f}  {r['status']}")
    if opts["show"]:
        print(f"    trace: {r['trace']}")
        print(f"    pergunta: {last_user_message(r['input'])[:500]}")
        print(f"    resposta: {(answer(r['output']) if r['status'] == 'ok' else r['error'])[:500]}")
print(f"\n{len(rows)} chamada(s)")
PY
