#!/usr/bin/env bash
# Verifica a segmentação de redes (docs/architecture.md → Redes).
# Cada "deve falhar" tem um controle positivo ("deve conectar") feito com a
# mesma ferramenta, para provar que a falha vem da rede e não do teste.
# Uso: tests/smoke/network-isolation.sh
set -uo pipefail
cd "$(dirname "$0")/../.."

PASS=0; FAIL=0
check() { # descrição, esperado (open|closed), comando...
  local desc="$1" expected="$2"; shift 2
  local got
  if "$@" >/dev/null 2>&1; then got=open; else got=closed; fi
  if [[ "$got" == "$expected" ]]; then
    echo "  ✔ $desc"; PASS=$((PASS + 1))
  else
    echo "  ✘ $desc (esperado $expected, obtido $got)"; FAIL=$((FAIL + 1))
  fi
}

# Conexão TCP a partir de containers com node (librechat), python (litellm,
# presidio) e bash (local-ai). Sucesso = conexão estabelecida em até 6 s.
from_node()   { docker compose exec -T "$1" node -e "const s=require('net').connect({host:'$2',port:$3});s.setTimeout(6000,()=>process.exit(1));s.on('connect',()=>process.exit(0));s.on('error',()=>process.exit(1))"; }
from_python() { docker compose exec -T "$1" python3 -c "import socket;socket.create_connection(('$2',$3),timeout=6)"; }
from_bash()   { docker compose exec -T "$1" timeout 8 bash -c "</dev/tcp/$2/$3"; }

echo "== Portal (librechat)"
check "librechat → litellm:4000 deve conectar (controle)"       open   from_node librechat litellm 4000
check "librechat → local-ai:11434 deve falhar"                   closed from_node librechat local-ai 11434
check "librechat → presidio-analyzer:3000 deve falhar"           closed from_node librechat presidio-analyzer 3000
check "librechat → postgres:5432 deve falhar"                    closed from_node librechat postgres 5432
check "librechat → clickhouse:8123 deve falhar"                  closed from_node librechat clickhouse 8123
check "librechat → langfuse-worker:3030 deve falhar"             closed from_node librechat langfuse-worker 3030

echo "== IA Local (local-ai)"
check "litellm → local-ai:11434 deve conectar (controle)"        open   from_python litellm local-ai 11434
check "local-ai → internet (1.1.1.1:443) deve falhar"            closed from_bash local-ai 1.1.1.1 443
check "local-ai → internet (generativelanguage.googleapis.com:443) deve falhar" closed from_bash local-ai generativelanguage.googleapis.com 443
check "local-ai → mongodb:27017 deve falhar"                     closed from_bash local-ai mongodb 27017

echo "== Presidio"
check "presidio-analyzer → internet (1.1.1.1:443) deve falhar"   closed from_python presidio-analyzer 1.1.1.1 443

echo "== Gateway (litellm)"
check "litellm → internet (generativelanguage.googleapis.com:443) deve conectar (controle)" open from_python litellm generativelanguage.googleapis.com 443
check "litellm → mongodb:27017 deve falhar"                      closed from_python litellm mongodb 27017

echo "== Portal de auditoria (audit-portal, Fase 9)"
check "audit-portal → postgres:5432 deve conectar (controle, trilha)"   open   from_python audit-portal postgres 5432
check "audit-portal → langfuse-web:3000 deve conectar (controle)"      open   from_python audit-portal langfuse-web 3000
check "audit-portal → clickhouse:8123 deve falhar"                     closed from_python audit-portal clickhouse 8123
check "audit-portal → redis:6379 deve falhar"                          closed from_python audit-portal redis 6379
check "audit-portal → minio:9000 deve falhar"                          closed from_python audit-portal minio 9000
check "audit-portal → local-ai:11434 deve falhar"                      closed from_python audit-portal local-ai 11434
check "audit-portal → mongodb:27017 deve falhar"                       closed from_python audit-portal mongodb 27017

echo
echo "Resultado: $PASS ok, $FAIL falha(s)"
[[ "$FAIL" -eq 0 ]]
