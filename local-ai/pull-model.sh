#!/bin/sh
# Executado pelo serviço one-shot local-ai-pull (único com saída à internet).
# Baixa LOCAL_AI_MODEL para o volume compartilhado com local-ai; se o modelo já
# estiver presente, termina sem acessar a rede.
set -eu

: "${LOCAL_AI_MODEL:?defina LOCAL_AI_MODEL}"

ollama serve >/tmp/ollama-serve.log 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT

i=0
until ollama list >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "pull-model: servidor Ollama não iniciou" >&2
    cat /tmp/ollama-serve.log >&2
    exit 1
  fi
  sleep 1
done

if ollama show "$LOCAL_AI_MODEL" >/dev/null 2>&1; then
  echo "pull-model: modelo $LOCAL_AI_MODEL já presente; nada a baixar."
else
  echo "pull-model: baixando $LOCAL_AI_MODEL..."
  # Até 5 tentativas com espera crescente: o registro do Ollama pode responder
  # 5xx temporariamente.
  attempt=1
  until ollama pull "$LOCAL_AI_MODEL"; do
    if [ "$attempt" -ge 5 ]; then
      echo "pull-model: falha ao baixar $LOCAL_AI_MODEL após $attempt tentativas" >&2
      exit 1
    fi
    wait_s=$((attempt * 10))
    echo "pull-model: tentativa $attempt falhou; nova tentativa em ${wait_s}s..." >&2
    sleep "$wait_s"
    attempt=$((attempt + 1))
  done
  echo "pull-model: download concluído."
fi
