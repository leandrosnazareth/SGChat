"""Metadados de auditoria para o Langfuse (AI Gateway, Fase 8).

O LiteLLM envia cada chamada ao Langfuse pelo callback nativo `langfuse` (trace com usuário,
sessão/conversa, entrada/saída, tokens, custo, latência e erros). Este módulo só acrescenta o
que é nosso, em dois pontos da cadeia de hooks (litellm/config.yaml):

- `custom.audit_metadata.start` (primeiro): remove do pedido do cliente as chaves que mudariam
  o registro (mascaramento, trace_*, tags, cabeçalhos de redação) e liga `trace_metadata` ao
  MESMO dicionário `spend_logs_metadata` que os demais hooks preenchem. Assim o trace traz a
  decisão final (classification, requested/effective_model, routing_reason…) mesmo quando um
  hook posterior bloqueia a requisição com exceção.
- `custom.audit_metadata.finish` (último): acrescenta tags pesquisáveis com a decisão.

O conteúdo de requisições bloqueadas por RESTRICTED é removido pelo próprio Security Router
antes do 403 (segredos não vão para o Langfuse). Spec: openspec/specs/audit-trail.
Carregado pelo LiteLLM pelo caminho do arquivo (sem `from __future__ import annotations`).
"""

import logging

from litellm.integrations.custom_logger import CustomLogger

TRACE_NAME = "corporate-ai-chat"
AUDIT_VERSION = 1
# Chaves de metadata que o callback do Langfuse interpreta e que o cliente não pode definir.
STEERING_KEYS = {"tags", "mask_input", "mask_output", "existing_trace_id", "generation_name",
                 "generation_id", "parent_observation_id", "debug_langfuse", "turn_off_message_logging",
                 "langfuse_masking_function"}
REDACTION_HEADERS = {"litellm-enable-message-redaction", "x-litellm-enable-message-redaction",
                     "litellm-disable-message-redaction"}
TAG_FIELDS = (("classification", "classification"), ("requested_model", "requested"),
              ("effective_model", "effective"), ("routing_reason", "routing"))

logger = logging.getLogger("corporate.audit_metadata")


def sanitize(data: dict) -> list:
    """Remove do pedido as chaves que alterariam o registro de auditoria. Devolve as removidas."""
    removed = []
    for key in [k for k in data if k == "turn_off_message_logging" or k.startswith("langfuse_")]:
        data.pop(key, None)
        removed.append(key)
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        # trace_id igual ao session_id é o que o próprio LiteLLM define a partir do cabeçalho de sessão.
        own_trace = metadata.get("trace_id") is not None and metadata.get("trace_id") == metadata.get("session_id")
        for key in [k for k in metadata if k in STEERING_KEYS or (k.startswith("trace_") and not (k == "trace_id" and own_trace))]:
            metadata.pop(key, None)
            removed.append(f"metadata.{key}")
        headers = metadata.get("headers")
        if isinstance(headers, dict):
            for key in [k for k in headers if str(k).lower() in REDACTION_HEADERS]:
                headers.pop(key, None)
                removed.append(f"header.{key}")
    return removed


def audit_tags(meta: dict) -> list:
    tags = [f"{label}:{meta[field]}" for field, label in TAG_FIELDS if meta.get(field)]
    if meta.get("content_redacted"):
        tags.append("content:redacted")
    return tags


class AuditStart(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type):
        removed = sanitize(data)
        if removed:
            logger.warning("audit_metadata: chaves de registro enviadas pelo cliente foram ignoradas: %s",
                           ",".join(sorted(removed)))
        metadata = data.setdefault("metadata", {})
        meta = metadata.setdefault("spend_logs_metadata", {})
        meta["audit_version"] = AUDIT_VERSION
        metadata["trace_metadata"] = meta      # mesma referência: reflete as decisões seguintes
        metadata["trace_name"] = TRACE_NAME
        return data


class AuditFinish(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type):
        metadata = data.setdefault("metadata", {})
        meta = metadata.get("spend_logs_metadata") or {}
        metadata["tags"] = audit_tags(meta)
        return data


start = AuditStart()
finish = AuditFinish()
