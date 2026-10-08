"""Leitura do registro de auditoria no Langfuse (API pública v4: /api/public/v2/observations).

Uma linha por chamada (trace). Busca sem conteúdo nem pede entrada/saída ao Langfuse;
só a visualização e a exportação de conversas pedem.
"""

import json
from datetime import datetime

import httpx

CLASSIFICATIONS = {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"}
LOCAL_MODELS = {"local-ai"}
META_FIELDS = "core,basic,metadata,model,usage,metrics,trace_context"
CONTENT_FIELDS = META_FIELDS + ",io"
FILTER_KEYS = ("user", "conversation", "date_from", "date_to", "classification", "requested", "effective",
               "reason", "blocked", "external", "text")


class RegistryUnavailable(RuntimeError):
    """Langfuse indisponível ou respondeu erro."""


class InvalidFilter(ValueError):
    """Filtro de busca inválido."""


def clean_filters(params: dict) -> dict:
    """Valida e normaliza os filtros recebidos; devolve só os preenchidos (vão para a trilha)."""
    out = {}
    for key in FILTER_KEYS:
        value = params.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if key in ("blocked", "external"):
            if str(value).lower() in ("1", "true", "on", "yes", "sim"):
                out[key] = True
            continue
        value = str(value).strip()
        if len(value) > 200:
            raise InvalidFilter(f"{key}: até 200 caracteres")
        if key == "classification":
            value = value.upper()
            if value not in CLASSIFICATIONS:
                raise InvalidFilter("classification: PUBLIC, INTERNAL, CONFIDENTIAL ou RESTRICTED")
        if key == "reason":
            value = value.upper()
        if key in ("date_from", "date_to"):
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as err:
                raise InvalidFilter(f"{key}: data ISO 8601 (ex.: 2026-10-07)") from err
        out[key] = value
    return out


def build_filters(filters: dict) -> list:
    """Filtros no formato da API v2 do Langfuse."""
    def meta(key, value):
        return {"type": "stringObject", "column": "metadata", "key": key, "operator": "=", "value": value}

    def when(value, end):
        return value + ("T23:59:59.999Z" if end else "T00:00:00Z") if len(value) == 10 else value

    out = [{"type": "string", "column": "type", "operator": "=", "value": "GENERATION"}]
    if "user" in filters:
        out.append({"type": "string", "column": "userId", "operator": "=", "value": filters["user"]})
    if "conversation" in filters:
        out.append({"type": "string", "column": "sessionId", "operator": "=", "value": filters["conversation"]})
    if "date_from" in filters:
        out.append({"type": "datetime", "column": "startTime", "operator": ">=", "value": when(filters["date_from"], False)})
    if "date_to" in filters:
        out.append({"type": "datetime", "column": "startTime", "operator": "<=", "value": when(filters["date_to"], True)})
    for key, field in (("classification", "classification"), ("requested", "requested_model"),
                       ("effective", "effective_model"), ("reason", "routing_reason")):
        if key in filters:
            out.append(meta(field, filters[key]))
    if filters.get("blocked"):
        out.append(meta("blocked", "true"))
    if "text" in filters:
        out.append({"type": "string", "column": "input", "operator": "matches", "value": filters["text"]})
    return out


def _messages(raw) -> list:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data.get("messages") or [] if isinstance(data, dict) else []
    except Exception:
        return []


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return "" if content is None else str(content)


def question(raw) -> str:
    users = [m for m in _messages(raw) if isinstance(m, dict) and m.get("role") == "user"]
    return _text(users[-1].get("content")) if users else (raw if isinstance(raw, str) else "")


def answer(raw) -> str:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, dict):
            return _text(data.get("content"))
    except Exception:
        pass
    return raw or ""


def summarize(obs: dict) -> dict:
    """Metadados de uma chamada — nunca inclui entrada nem saída."""
    m = obs.get("metadata") or {}
    blocked = str(m.get("blocked")).lower() == "true"
    reasons = m.get("security_reasons")
    if isinstance(reasons, str):
        try:
            reasons = json.loads(reasons)
        except Exception:
            reasons = [reasons]
    return {
        "time": (obs.get("startTime") or "")[:19].replace("T", " "),
        "trace_id": obs.get("traceId"),
        "user": obs.get("userId") or "",
        "conversation_id": obs.get("sessionId") or "",
        "requested_model": m.get("requested_model") or "",
        "effective_model": m.get("effective_model") or "",
        "classification": m.get("classification") or "",
        "routing_reason": m.get("routing_reason") or "",
        "security_reasons": reasons or [],
        "external": bool(m.get("effective_model")) and m.get("effective_model") not in LOCAL_MODELS,
        "tokens": obs.get("totalUsage") or 0,
        "cost_usd": obs.get("totalCost") or 0,
        "latency_s": obs.get("latency"),
        "status": "BLOQUEADO" if blocked else ("ERRO" if obs.get("level") == "ERROR" else "ok"),
    }


def with_content(obs: dict) -> dict:
    row = summarize(obs)
    row.update({"question": question(obs.get("input")),
                "answer": answer(obs.get("output")) if row["status"] == "ok" else (obs.get("statusMessage") or ""),
                "input": obs.get("input"), "output": obs.get("output")})
    return row


class Registry:
    def __init__(self, url: str, public_key: str, secret_key: str, timeout: float = 20.0, transport=None):
        self.url = url.rstrip("/")
        self.auth = (public_key, secret_key)
        self.timeout, self.transport = timeout, transport

    async def _observations(self, filters: list, limit: int, fields: str) -> list:
        params = {"fields": fields, "limit": str(min(1000, limit * 2)), "filter": json.dumps(filters),
                  "expandMetadata": "security_reasons,auto_decision"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, auth=self.auth, transport=self.transport) as client:
                resp = await client.get(f"{self.url}/api/public/v2/observations", params=params)
            if resp.status_code != 200:
                raise RegistryUnavailable(f"Langfuse respondeu {resp.status_code}")
            data = resp.json().get("data", [])
        except httpx.HTTPError as err:
            raise RegistryUnavailable(str(err)) from err
        # O LiteLLM registra falhas geradas no gateway duas vezes no mesmo trace: uma linha por trace.
        seen, rows = set(), []
        for obs in data:
            if obs.get("traceId") in seen:
                continue
            seen.add(obs.get("traceId"))
            rows.append(obs)
            if len(rows) >= limit:
                break
        return rows

    async def search(self, filters: dict, limit: int) -> list:
        rows = [summarize(o) for o in await self._observations(build_filters(filters), limit, META_FIELDS)]
        return [r for r in rows if r["external"]] if filters.get("external") else rows

    async def calls(self, conversation: str | None = None, trace: str | None = None, limit: int = 200) -> list:
        """Chamadas de uma conversa (sessão) ou de um trace, em ordem cronológica, com conteúdo."""
        filters = [{"type": "string", "column": "type", "operator": "=", "value": "GENERATION"}]
        if conversation:
            filters.append({"type": "string", "column": "sessionId", "operator": "=", "value": conversation})
        elif trace:
            filters.append({"type": "string", "column": "traceId", "operator": "=", "value": trace})
        else:
            raise InvalidFilter("informe a conversa ou o trace")
        observations = await self._observations(filters, limit, CONTENT_FIELDS)
        return [with_content(o) for o in sorted(observations, key=lambda o: o.get("startTime") or "")]
