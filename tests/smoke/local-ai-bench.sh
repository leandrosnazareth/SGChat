#!/usr/bin/env bash
# Chat de teste e medição de latência da IA Local, executado de dentro da rede
# ai-net (via container presidio-anonymizer, que possui curl). Uso:
#   tests/smoke/local-ai-bench.sh [repetições]
set -euo pipefail
export LC_NUMERIC=C
cd "$(dirname "$0")/../.."

RUNS="${1:-3}"
MODEL="$(docker compose exec -T local-ai printenv LOCAL_AI_MODEL)"
BODY=$(printf '{"model":"%s","messages":[{"role":"user","content":"Explique em três frases o que é um garbage collector."}],"max_tokens":120,"temperature":0}' "$MODEL")

call() {
  docker compose exec -T presidio-anonymizer curl -sS -m 300 \
    -o /tmp/local-ai-resp.json -w '%{http_code} %{time_total}' \
    -H 'Content-Type: application/json' -d "$BODY" \
    http://local-ai:11434/v1/chat/completions
}

echo "modelo: $MODEL"
read -r code _ <<<"$(call)"   # aquecimento (carrega o modelo na memória)
[[ "$code" == 200 ]] || { echo "FALHA: aquecimento retornou HTTP $code"; exit 1; }

total=0
for i in $(seq 1 "$RUNS"); do
  read -r code secs <<<"$(call)"
  [[ "$code" == 200 ]] || { echo "FALHA: HTTP $code"; exit 1; }
  tokens=$(docker compose exec -T presidio-anonymizer python3 -c \
    "import json;print(json.load(open('/tmp/local-ai-resp.json'))['usage']['completion_tokens'])")
  printf 'execução %d: %.2fs, %s tokens, %.1f tokens/s\n' "$i" "$secs" "$tokens" "$(echo "$tokens / $secs" | bc -l)"
  total=$(echo "$total + $secs" | bc -l)
done
printf 'média: %.2fs\n' "$(echo "$total / $RUNS" | bc -l)"
docker compose exec -T local-ai ollama ps
docker compose exec -T presidio-anonymizer python3 -c \
  "import json;d=json.load(open('/tmp/local-ai-resp.json'));print('resposta:', d['choices'][0]['message']['content'][:200].replace(chr(10),' '))"
