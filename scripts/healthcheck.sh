#!/usr/bin/env bash
# Verifica a saúde da stack e as portas publicadas no host.
# Falha (exit 1) se:
#   - algum serviço de longa duração não estiver running + healthy;
#   - algum serviço one-shot não tiver terminado com exit 0;
#   - houver porta publicada além de LibreChat, Langfuse, portal de auditoria e 127.0.0.1:LITELLM_PORT.
# Uso: scripts/healthcheck.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

PS_JSON="$(docker compose ps -a --format json)" \
EXPECTED="$(docker compose config --services | tr '\n' ' ')" \
python3 - <<'PY'
import json
import os
import sys

ONE_SHOT = {"local-ai-pull", "litellm-bootstrap", "audit-db-init"}
expected = set(os.environ["EXPECTED"].split())
librechat = os.environ.get("LIBRECHAT_PORT", "3080")
langfuse = os.environ.get("LANGFUSE_PORT", "3000")
litellm = os.environ.get("LITELLM_PORT", "4000")
audit = os.environ.get("AUDIT_PORTAL_PORT", "3090")
allowed = {
    ("0.0.0.0", librechat), ("::", librechat),
    ("0.0.0.0", langfuse), ("::", langfuse),
    ("0.0.0.0", audit), ("::", audit),
    ("127.0.0.1", litellm),
}

rows = [json.loads(line) for line in os.environ["PS_JSON"].splitlines() if line.strip()]
problems = [f"{svc}: container inexistente" for svc in sorted(expected - {r["Service"] for r in rows})]
published = set()

for r in sorted(rows, key=lambda r: r["Service"]):
    svc, state, health, code = r["Service"], r["State"], r.get("Health") or "", r.get("ExitCode")
    if svc in ONE_SHOT:
        ok = state == "exited" and code == 0
        status = f"{state} (exit {code})"
    else:
        ok = state == "running" and health == "healthy"
        status = f"{state}/{health or 'sem health check'}"
    print(f"  {'✔' if ok else '✘'} {svc:<22} {status}")
    if not ok:
        problems.append(f"{svc}: {status}")
    for p in r.get("Publishers") or []:
        if p.get("PublishedPort"):
            bind = (p.get("URL") or "0.0.0.0", str(p["PublishedPort"]))
            published.add(f"{bind[0]}:{bind[1]}")
            if bind not in allowed:
                problems.append(f"{svc}: porta não permitida publicada em {bind[0]}:{bind[1]}")

print("  portas publicadas no host:", ", ".join(sorted(published)) or "nenhuma")
if problems:
    print("\nFALHA:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("\nOK: todos os serviços saudáveis e apenas as portas esperadas publicadas.")
PY
