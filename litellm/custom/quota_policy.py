"""Cotas de tokens e budgets em US$ por usuário/grupo/global (AI Gateway, Fase 4).

Registrado em litellm/config.yaml como `custom.quota_policy.handler`, DEPOIS de
`custom.model_access.handler` (permissão antes de cota). Antes de cada chamada a
um modelo com cota, apura o consumo das regras aplicáveis (litellm/policies/quotas.yaml)
e, se alguma estiver esgotada:
- LOCAL_FALLBACK: redireciona a requisição para a IA Local (se a política de acesso
  permitir ao usuário);
- BLOCK: HTTP 429 com TOKEN_QUOTA_EXCEEDED ou BUDGET_EXCEEDED.
Registra `requested_model`, `effective_model`, `routing_reason`, `quota_rule` e
`groups` em metadata.spend_logs_metadata (gravado em LiteLLM_SpendLogs).
Fail-closed: política inválida ou consumo não apurável → ação configurada.

Consumo = banco (LiteLLM_SpendLogs, gravado em lote a cada ~10 s) até agora−30 s
+ memória (eventos de sucesso deste processo) a partir de agora−30 s.
Spec: openspec/specs/usage-quotas. Carregado pelo LiteLLM pelo caminho do arquivo,
sem registro em sys.modules (por isso sem `from __future__ import annotations`).
"""

import importlib.util
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml
from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger

POLICY_PATH = os.environ.get("QUOTA_POLICY_PATH", "/app/policies/quotas.yaml")
ACCESS_MODULE_PATH = os.environ.get(
    "MODEL_ACCESS_MODULE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_access.py")
)
QUOTA_TZ = ZoneInfo(os.environ.get("QUOTA_TIMEZONE", "America/Sao_Paulo"))
ACTIONS = ("LOCAL_FALLBACK", "BLOCK")
PERIODS = ("daily", "weekly", "monthly")
ADMIN_ROLE = "proxy_admin"
# Usados quando a política de cotas está indisponível (fail-closed).
SAFE_FALLBACK_MODEL = "local-ai"
SETTLE_SECONDS = 30      # janela coberta pela memória (atraso de gravação do banco ~10 s)
MEMORY_TTL_SECONDS = 120
DB_CACHE_SECONDS = 3

TOKEN_QUOTA_EXCEEDED = "TOKEN_QUOTA_EXCEEDED"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
QUOTA_CHECK_UNAVAILABLE = "QUOTA_CHECK_UNAVAILABLE"
DIRECT = "DIRECT"

logger = logging.getLogger("corporate.quota_policy")


def _env_action(name: str) -> str:
    value = os.environ.get(name, "LOCAL_FALLBACK").strip().upper()
    return value if value in ACTIONS else "LOCAL_FALLBACK"


TOKEN_QUOTA_ACTION = _env_action("TOKEN_QUOTA_ACTION")
BUDGET_ACTION = _env_action("BUDGET_ACTION")


class QuotaPolicyError(ValueError):
    """Política de cotas ausente ou estruturalmente inválida."""


@dataclass(frozen=True)
class Rule:
    name: str
    target_kind: str            # user | group | all_users | global
    target_value: str | None    # e-mail (minúsculo) ou nome do grupo
    models: frozenset | None    # None = todos os externos (não ilimitados)
    period: str
    max_tokens: int | None
    max_cost_usd: float | None
    action: str | None

    def covers(self, model: str) -> bool:
        return self.models is None or model in self.models

    def targets(self, user: str, groups: tuple) -> bool:
        if self.target_kind in ("all_users", "global"):
            return True
        if self.target_kind == "user":
            return self.target_value == user
        return self.target_value in groups


@dataclass(frozen=True)
class QuotaConfig:
    fallback_model: str
    unlimited_models: frozenset
    rules: tuple

    def applicable(self, user: str, groups: tuple, model: str) -> list:
        if model in self.unlimited_models:
            return []
        return [r for r in self.rules if r.covers(model) and r.targets(user, groups)]


def _limit(value, name: str, kind):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise QuotaPolicyError(f"regra {name!r}: limite inválido {value!r}")
    return kind(value)


def parse_quotas(raw: object) -> QuotaConfig:
    """Valida a política; qualquer violação gera QuotaPolicyError."""
    if not isinstance(raw, dict):
        raise QuotaPolicyError("a política deve ser um mapeamento YAML")
    if raw.get("version") != 1:
        raise QuotaPolicyError(f"versão de política não suportada: {raw.get('version')!r}")
    fallback = raw.get("fallback_model")
    if not isinstance(fallback, str) or not fallback:
        raise QuotaPolicyError("'fallback_model' é obrigatório")
    unlimited = raw.get("unlimited_models")
    if not isinstance(unlimited, list) or not all(isinstance(m, str) and m for m in unlimited):
        raise QuotaPolicyError("'unlimited_models' deve ser uma lista de nomes")
    if fallback not in unlimited:
        raise QuotaPolicyError("'fallback_model' deve estar em 'unlimited_models' (evita redirecionamento em cadeia)")
    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list):
        raise QuotaPolicyError("'rules' deve ser uma lista")

    rules, names = [], set()
    for item in rules_raw:
        if not isinstance(item, dict):
            raise QuotaPolicyError("cada regra deve ser um mapeamento")
        name = item.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise QuotaPolicyError(f"regra sem 'name' ou com nome repetido: {name!r}")
        names.add(name)

        target = item.get("applies_to")
        if target in ("all_users", "global"):
            kind, value = target, None
        elif isinstance(target, str) and target.startswith(("user:", "group:")) and target.split(":", 1)[1].strip():
            kind, value = target.split(":", 1)
            value = value.strip().lower() if kind == "user" else value.strip()
        else:
            raise QuotaPolicyError(f"regra {name!r}: 'applies_to' inválido {target!r}")

        models = item.get("models")
        if models == "external":
            models_set = None
        elif isinstance(models, list) and models and all(isinstance(m, str) and m for m in models):
            if set(models) & set(unlimited):
                raise QuotaPolicyError(f"regra {name!r}: modelos ilimitados não podem ter cota")
            models_set = frozenset(models)
        else:
            raise QuotaPolicyError(f"regra {name!r}: 'models' deve ser uma lista ou 'external'")

        period = item.get("period")
        if period not in PERIODS:
            raise QuotaPolicyError(f"regra {name!r}: 'period' deve ser um de {PERIODS}")
        max_tokens = _limit(item.get("max_tokens"), name, int)
        max_cost = _limit(item.get("max_cost_usd"), name, float)
        if max_tokens is None and max_cost is None:
            raise QuotaPolicyError(f"regra {name!r}: informe 'max_tokens' e/ou 'max_cost_usd'")
        action = item.get("action")
        if action is not None and action not in ACTIONS:
            raise QuotaPolicyError(f"regra {name!r}: 'action' deve ser um de {ACTIONS}")

        rules.append(Rule(name, kind, value, models_set, period, max_tokens, max_cost, action))
    return QuotaConfig(fallback, frozenset(unlimited), tuple(rules))


def period_start(period: str, now: datetime, tz: ZoneInfo = QUOTA_TZ) -> datetime:
    """Início (UTC) do período de calendário corrente no fuso `tz`."""
    local = now.astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "weekly":
        start -= timedelta(days=start.weekday())
    elif period == "monthly":
        start = start.replace(day=1)
    return start.astimezone(timezone.utc)


class MemoryUsage:
    """Consumo recente deste processo (cobre o atraso de gravação do banco)."""

    def __init__(self):
        self._events = deque()
        self._lock = threading.Lock()

    def add(self, started_at: float, user: str | None, model: str, tokens: int, cost: float) -> None:
        with self._lock:
            self._events.append((started_at, (user or "").lower(), model, tokens, cost))
            limit = time.time() - MEMORY_TTL_SECONDS
            while self._events and self._events[0][0] < limit:
                self._events.popleft()

    def total(self, since: float, user: str | None, models: frozenset | None, excluded: frozenset):
        tokens, cost = 0, 0.0
        with self._lock:
            for started, u, model, t, c in self._events:
                if started < since or (user is not None and u != user):
                    continue
                if (models is not None and model not in models) or model in excluded:
                    continue
                tokens += t
                cost += c
        return tokens, cost


class DbUsage:
    """Consumo gravado em LiteLLM_SpendLogs, via cliente Prisma do próprio proxy."""

    def __init__(self):
        self._cache: dict = {}

    async def total(self, start: datetime, end: datetime, user: str | None, models: frozenset | None, excluded: frozenset):
        key = (start, user, models, excluded)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < DB_CACHE_SECONDS:
            return cached[1]
        from litellm.proxy.proxy_server import prisma_client
        if prisma_client is None:
            raise RuntimeError("banco do gateway indisponível")

        args = [start.replace(tzinfo=None).isoformat(), end.replace(tzinfo=None).isoformat()]
        where = ['status = \'success\'', '"startTime" >= $1::timestamp', '"startTime" < $2::timestamp']
        if user is not None:
            args.append(user)
            where.append(f"lower(end_user) = ${len(args)}")
        if models is not None:
            marks = []
            for m in sorted(models):
                args.append(m)
                marks.append(f"${len(args)}")
            where.append(f"model_group IN ({', '.join(marks)})")
        for m in sorted(excluded):
            args.append(m)
            where.append(f"model_group <> ${len(args)}")
        sql = (
            'SELECT COALESCE(SUM(total_tokens), 0)::bigint AS tokens, COALESCE(SUM(spend), 0)::float AS cost '
            f'FROM "LiteLLM_SpendLogs" WHERE {" AND ".join(where)}'
        )
        rows = await prisma_client.db.query_raw(sql, *args)
        row = rows[0] if rows else {"tokens": 0, "cost": 0.0}
        result = (int(row["tokens"] or 0), float(row["cost"] or 0.0))
        self._cache[key] = (time.monotonic(), result)
        return result


class QuotaStore:
    """Carrega a política de cotas e recarrega quando o arquivo muda."""

    def __init__(self, path: str):
        self.path = path
        self._signature = None
        self._config: QuotaConfig | None = None
        self._error: str | None = "política ainda não carregada"
        self._lock = threading.Lock()

    def get(self) -> QuotaConfig | None:
        try:
            st = os.stat(self.path)
            signature = (st.st_mtime, st.st_size)
        except OSError as err:
            self._fail(f"arquivo de cotas inacessível: {err}")
            return None
        if signature != self._signature:
            with self._lock:
                if signature != self._signature:
                    try:
                        with open(self.path, encoding="utf-8") as fh:
                            config = parse_quotas(yaml.safe_load(fh))
                    except (OSError, yaml.YAMLError, QuotaPolicyError) as err:
                        self._signature = signature
                        self._fail(f"política de cotas inválida em {self.path}: {err}")
                        return None
                    self._signature, self._config, self._error = signature, config, None
                    logger.warning(
                        "quota_policy: política carregada de %s (%d regras; fallback %s; ilimitados %s)",
                        self.path, len(config.rules), config.fallback_model, ",".join(sorted(config.unlimited_models)),
                    )
        return self._config

    def _fail(self, message: str) -> None:
        if message != self._error:
            logger.error("quota_policy: %s — chamadas a modelos com cota recebem a ação configurada", message)
        self._config, self._error = None, message


def _load_access_module(path: str):
    spec = importlib.util.spec_from_file_location("corporate_model_access_for_quota", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QuotaPolicy(CustomLogger):
    def __init__(self, store: QuotaStore, access_store=None, db: DbUsage | None = None, memory: MemoryUsage | None = None):
        super().__init__()
        self.store = store
        self.access_store = access_store
        self.db = db or DbUsage()
        self.memory = memory or MemoryUsage()
        self.store.get()

    # --- apuração -------------------------------------------------------------
    async def usage(self, rule: Rule, config: QuotaConfig, user: str, now: datetime):
        start = period_start(rule.period, now)
        settle = now - timedelta(seconds=SETTLE_SECONDS)
        scope_user = None if rule.target_kind == "global" else user
        excluded = config.unlimited_models if rule.models is None else frozenset()
        db_tokens, db_cost = (0, 0.0)
        if settle > start:
            db_tokens, db_cost = await self.db.total(start, settle, scope_user, rule.models, excluded)
        mem_since = max(start, settle).timestamp()
        mem_tokens, mem_cost = self.memory.total(mem_since, scope_user, rule.models, excluded)
        return db_tokens + mem_tokens, db_cost + mem_cost

    async def evaluate(self, config: QuotaConfig, user: str, groups: tuple, model: str, now: datetime | None = None):
        """Retorna (motivo, regra) da primeira violação — tokens têm prioridade — ou (None, None)."""
        now = now or datetime.now(timezone.utc)
        budget_hit = None
        for rule in config.applicable(user, groups, model):
            tokens, cost = await self.usage(rule, config, user, now)
            if rule.max_tokens is not None and tokens >= rule.max_tokens:
                return TOKEN_QUOTA_EXCEEDED, rule
            if budget_hit is None and rule.max_cost_usd is not None and cost >= rule.max_cost_usd:
                budget_hit = rule
        return (BUDGET_EXCEEDED, budget_hit) if budget_hit else (None, None)

    # --- hooks do LiteLLM -----------------------------------------------------
    def _access_policy(self):
        return self.access_store.get() if self.access_store is not None else None

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        model = data.get("model")
        role = getattr(user_api_key_dict, "user_role", None)
        user = getattr(user_api_key_dict, "end_user_id", None)
        if model is None or getattr(role, "value", role) == ADMIN_ROLE or not isinstance(user, str) or not user.strip():
            return data
        user = user.strip().lower()
        access = self._access_policy()
        groups = tuple(access.groups_for(user)) if access is not None else ()
        meta = data.setdefault("metadata", {}).setdefault("spend_logs_metadata", {})
        # Não sobrescreve o que o Security Router (que roda antes) já registrou.
        meta.setdefault("requested_model", model)
        meta.setdefault("routing_reason", DIRECT)
        meta.update({"effective_model": model, "groups": list(groups)})

        config = self.store.get()
        if config is None:
            if model == SAFE_FALLBACK_MODEL:
                return data
            reason, rule = QUOTA_CHECK_UNAVAILABLE, None
            fallback = SAFE_FALLBACK_MODEL
        else:
            if model in config.unlimited_models:
                return data
            fallback = config.fallback_model
            try:
                reason, rule = await self.evaluate(config, user, groups, model)
            except Exception as err:  # banco indisponível etc.: fail-closed
                logger.error("quota_policy: falha ao apurar consumo de %s: %s", user, err)
                reason, rule = QUOTA_CHECK_UNAVAILABLE, None
        if reason is None:
            return data

        default_action = BUDGET_ACTION if reason == BUDGET_EXCEEDED else TOKEN_QUOTA_ACTION
        action = (rule.action if rule and rule.action else default_action)
        rule_name = rule.name if rule else None
        meta.update({"routing_reason": reason, "quota_rule": rule_name})

        if action == "LOCAL_FALLBACK":
            if access is not None and fallback in access.allowed_models(user):
                data["model"] = fallback
                meta["effective_model"] = fallback
                logger.warning(
                    "quota_policy: FALLBACK user=%s requested=%s effective=%s reason=%s rule=%s",
                    user, model, fallback, reason, rule_name or "-",
                )
                return data
            logger.warning("quota_policy: fallback para %s não permitido a %s — bloqueando", fallback, user)

        meta["blocked"] = True
        meta.pop("effective_model", None)
        logger.warning("quota_policy: BLOQUEADO user=%s model=%s reason=%s rule=%s", user, model, reason, rule_name or "-")
        raise HTTPException(status_code=429, detail={"error": reason, "rule": rule_name, "model": model})

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        slo = kwargs.get("standard_logging_object") or {}
        model = slo.get("model_group")
        if not model:
            return
        started = start_time
        if isinstance(started, datetime):
            started = (started if started.tzinfo else started.replace(tzinfo=timezone.utc)).timestamp()
        elif not isinstance(started, (int, float)):
            started = time.time()
        self.memory.add(
            started, slo.get("end_user"), model,
            int(slo.get("total_tokens") or 0), float(slo.get("response_cost") or 0.0),
        )


def _build_handler() -> QuotaPolicy:
    access_store = None
    try:
        access_module = _load_access_module(ACCESS_MODULE_PATH)
        access_store = access_module.PolicyStore(access_module.POLICY_PATH)
    except Exception as err:
        logger.error("quota_policy: política de acesso indisponível (%s); fallback será bloqueado", err)
    return QuotaPolicy(QuotaStore(POLICY_PATH), access_store)


handler = _build_handler()
