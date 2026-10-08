#!/usr/bin/env bash
# Verifica a integridade da trilha de acesso dos auditores (banco audit, Fase 9).
# Recalcula a cadeia de hashes inteira e confere a cabeça com a cópia no log do portal.
# Exit 0 = íntegra; 1 = adulterada ou inconsistente. Guia: docs/audit.md
# Uso: scripts/audit-access-verify.sh
set -euo pipefail
cd "$(dirname "$0")/.."

read -r total invalid head < <(docker compose exec -T postgres psql -U postgres -d audit -tA -F ' ' \
  -c "SELECT total, coalesce(first_invalid_id::text, '-'), coalesce(head_hash, '-') FROM audit.verify_access_chain()")
last_id=$(docker compose exec -T postgres psql -U postgres -d audit -tA -c "SELECT coalesce(max(id), 0) FROM audit.access_log")
echo "eventos: $total · último id: $last_id · hash da cabeça: $head"

status=0
if [[ "$invalid" != "-" ]]; then
  echo "ADULTERADA: o evento #$invalid não confere com a cadeia (alterado, removido ou inserido fora da função)."
  status=1
else
  echo "cadeia íntegra."
fi

# Cópia fora do banco: o portal registra cada evento no log do container. Um evento no log
# com id maior que o último do banco indica remoção dos eventos mais recentes.
logged=$(docker compose logs --no-log-prefix audit-portal 2>/dev/null | sed -n 's/.*AUDIT_ACCESS id=\([0-9]*\) .*/\1/p' | sort -n | tail -1)
if [[ -n "$logged" && "$logged" -gt "$last_id" ]]; then
  echo "INCONSISTENTE: o log do portal tem o evento #$logged, mas o banco termina em #$last_id."
  status=1
elif [[ -n "$logged" ]]; then
  echo "log do portal confere (último evento registrado no log: #$logged)."
fi
exit $status
