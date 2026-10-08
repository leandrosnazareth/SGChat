#!/usr/bin/env bash
# Relatório de consumo e custo do AI Gateway (requisições bem-sucedidas em
# LiteLLM_SpendLogs), no fuso QUOTA_TIMEZONE (padrão America/Sao_Paulo).
#
#   scripts/usage-report.sh [--by user|group|model|provider] [--period day|week|month]
#
# --by     agrupamento (padrão: user). "group" usa os grupos gravados no momento de
#          cada requisição; um usuário em vários grupos conta em cada um deles.
# --period período corrente: day, week (desde segunda-feira) ou month (padrão).
set -euo pipefail
cd "$(dirname "$0")/.."

BY=user; PERIOD=month
while [[ $# -gt 0 ]]; do
  case "$1" in
    --by) BY="${2:-}"; shift 2 ;;
    --period) PERIOD="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "argumento desconhecido: $1" >&2; exit 2 ;;
  esac
done
TZ_NAME="$(grep -E '^QUOTA_TIMEZONE=' .env 2>/dev/null | cut -d= -f2 || true)"
TZ_NAME="${TZ_NAME:-America/Sao_Paulo}"

case "$PERIOD" in day|week|month) ;; *) echo "--period deve ser day, week ou month" >&2; exit 2 ;; esac
case "$BY" in
  user)     KEY="coalesce(nullif(end_user, ''), '(sem usuário)')"; FROM='"LiteLLM_SpendLogs" s' ;;
  model)    KEY="coalesce(nullif(model_group, ''), '(sem modelo)')"; FROM='"LiteLLM_SpendLogs" s' ;;
  provider) KEY="coalesce(nullif(custom_llm_provider, ''), '(sem provedor)')"; FROM='"LiteLLM_SpendLogs" s' ;;
  group)    KEY="coalesce(g.grupo, '(sem registro)')"
            FROM='"LiteLLM_SpendLogs" s LEFT JOIN LATERAL jsonb_array_elements_text(
                    CASE WHEN jsonb_typeof(s.metadata::jsonb->'"'"'spend_logs_metadata'"'"'->'"'"'groups'"'"') = '"'"'array'"'"'
                         THEN s.metadata::jsonb->'"'"'spend_logs_metadata'"'"'->'"'"'groups'"'"' ELSE '"'"'[]'"'"'::jsonb END) AS g(grupo) ON true' ;;
  *) echo "--by deve ser user, group, model ou provider" >&2; exit 2 ;;
esac

SQL="
WITH janela AS (
  SELECT (date_trunc('$PERIOD', now() AT TIME ZONE '$TZ_NAME') AT TIME ZONE '$TZ_NAME') AT TIME ZONE 'UTC' AS inicio
)
SELECT $KEY AS \"$BY\",
       count(*) AS requisicoes,
       count(*) FILTER (WHERE coalesce(s.metadata::jsonb->'spend_logs_metadata'->>'routing_reason', 'DIRECT') <> 'DIRECT') AS redirecionadas,
       sum(s.prompt_tokens) AS prompt_tokens,
       sum(s.completion_tokens) AS completion_tokens,
       sum(s.total_tokens) AS total_tokens,
       round(sum(s.spend)::numeric, 6) AS custo_usd
FROM $FROM, janela
WHERE s.status = 'success' AND s.\"startTime\" >= janela.inicio
GROUP BY 1
ORDER BY custo_usd DESC, total_tokens DESC;"

echo "Consumo por $BY — período: $PERIOD corrente ($TZ_NAME)"
docker compose exec -T postgres psql -U postgres -d litellm -P footer=off -c "$SQL"
