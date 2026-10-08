"""ModelRouter — escolha automática do modelo para "auto" (AI Gateway, Fase 7).

Registrado em litellm/config.yaml como `custom.model_router.handler`, entre
`security_router` e `quota_policy`. Só atua quando o modelo pedido é `auto` (se a
segurança já mandou o conteúdo para a IA Local, não há o que escolher). Ordem:
permissão → cota/budget → complexidade → disponibilidade → custo/qualidade.
- candidatos: modelos de policies/routing.yaml permitidos ao usuário (política de acesso);
- exclui modelos com cota/budget esgotado (instância do quota_policy registrada no
  LiteLLM, que compartilha a janela de consumo recente) e modelos indisponíveis
  (falhas recentes → pausa temporária);
- faixa de complexidade por sinais determinísticos → qualidade mínima;
- escolhe o candidato mais barato que atinge a qualidade; senão, o de maior qualidade;
  sem candidato → fallback_model (se permitido) ou HTTP 403.
Registra requested_model, effective_model, routing_reason=AUTO e auto_decision em
metadata.spend_logs_metadata (sem o texto). Spec: openspec/specs/auto-routing.
Carregado pelo LiteLLM pelo caminho do arquivo (sem `from __future__ import annotations`).
"""

import importlib.util
import logging
import os
import re
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass

import yaml
from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger

POLICY_PATH = os.environ.get("ROUTING_POLICY_PATH", "/app/policies/routing.yaml")
ACCESS_MODULE_PATH = os.environ.get(
    "MODEL_ACCESS_MODULE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_access.py")
)
ADMIN_ROLE = "proxy_admin"
ROUTING_REASON = "AUTO"
QUOTA_EXCLUSION = {"TOKEN_QUOTA_EXCEEDED": "quota", "BUDGET_EXCEEDED": "budget", "QUOTA_CHECK_UNAVAILABLE": "quota_unknown"}

logger = logging.getLogger("corporate.model_router")


class RoutingPolicyError(ValueError):
    """Política de roteamento ausente ou inválida."""


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


@dataclass(frozen=True)
class Tier:
    name: str
    max_score: float | None
    min_quality: float


@dataclass(frozen=True)
class RoutingConfig:
    auto_model: str
    fallback_model: str
    models: dict            # nome → (quality, cost)
    tiers: tuple
    signals: dict
    terms: tuple            # padrões compilados (texto normalizado)
    availability: dict


def parse_routing(raw: object) -> RoutingConfig:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise RoutingPolicyError("política deve ser um mapeamento com version: 1")
    auto_model, fallback = raw.get("auto_model"), raw.get("fallback_model")
    if not isinstance(auto_model, str) or not auto_model or not isinstance(fallback, str) or not fallback:
        raise RoutingPolicyError("'auto_model' e 'fallback_model' são obrigatórios")
    models_raw = raw.get("models")
    if not isinstance(models_raw, dict) or not models_raw:
        raise RoutingPolicyError("'models' deve listar ao menos um candidato")
    models = {}
    for name, spec in models_raw.items():
        quality, cost = (spec or {}).get("quality"), (spec or {}).get("cost")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in (quality, cost)):
            raise RoutingPolicyError(f"modelo {name!r}: 'quality' e 'cost' numéricos ≥ 0")
        if name == auto_model:
            raise RoutingPolicyError("o próprio modelo auto não pode ser candidato")
        models[str(name)] = (float(quality), float(cost))

    complexity = raw.get("complexity") or {}
    tiers_raw = complexity.get("tiers")
    if not isinstance(tiers_raw, list) or not tiers_raw:
        raise RoutingPolicyError("'complexity.tiers' deve ser uma lista não vazia")
    tiers, previous = [], float("-inf")
    for i, t in enumerate(tiers_raw):
        max_score = t.get("max_score")
        last = i == len(tiers_raw) - 1
        if (max_score is None) != last:
            raise RoutingPolicyError("só a última faixa pode (e deve) não ter 'max_score'")
        if max_score is not None and max_score <= previous:
            raise RoutingPolicyError("'max_score' das faixas deve ser crescente")
        if not isinstance(t.get("name"), str) or not isinstance(t.get("min_quality"), (int, float)):
            raise RoutingPolicyError("cada faixa precisa de 'name' e 'min_quality'")
        tiers.append(Tier(t["name"], None if max_score is None else float(max_score), float(t["min_quality"])))
        previous = max_score if max_score is not None else previous

    signals = complexity.get("signals") or {}
    terms = tuple(
        re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in normalize(term).split()) + r"(?!\w)")
        for term in (signals.get("analysis_terms") or {}).get("terms") or [] if str(term).strip()
    )
    availability = raw.get("availability") or {}
    availability = {
        "failure_threshold": int(availability.get("failure_threshold", 2)),
        "window_seconds": float(availability.get("window_seconds", 120)),
        "cooldown_seconds": float(availability.get("cooldown_seconds", 60)),
    }
    return RoutingConfig(auto_model, fallback, models, tuple(tiers), signals, terms, availability)


class PolicyStore:
    def __init__(self, path: str):
        self.path, self._signature, self._config = path, None, None
        self._error = "política ainda não carregada"
        self._lock = threading.Lock()

    def get(self) -> RoutingConfig | None:
        try:
            st = os.stat(self.path)
            signature = (st.st_mtime, st.st_size)
        except OSError as err:
            return self._fail(f"arquivo de roteamento inacessível: {err}")
        if signature != self._signature:
            with self._lock:
                if signature != self._signature:
                    try:
                        with open(self.path, encoding="utf-8") as fh:
                            config = parse_routing(yaml.safe_load(fh))
                    except (OSError, yaml.YAMLError, RoutingPolicyError, AttributeError, TypeError, ValueError) as err:
                        self._signature = signature
                        return self._fail(f"política de roteamento inválida em {self.path}: {err}")
                    self._signature, self._config, self._error = signature, config, None
                    logger.warning(
                        "model_router: política carregada de %s (modelo %s; candidatos %s; faixas %s)",
                        self.path, config.auto_model, ",".join(config.models), ",".join(t.name for t in config.tiers),
                    )
        return self._config

    def _fail(self, message: str):
        if message != self._error:
            logger.error("model_router: %s — 'auto' usará o fallback", message)
        self._config, self._error = None, message
        return None


# --- complexidade -------------------------------------------------------------

def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def complexity(config: RoutingConfig, messages: list):
    """→ (faixa, pontuação, sinais disparados)."""
    user_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    text = _text_of(user_messages[-1].get("content")) if user_messages else ""
    s, score, fired = config.signals, 0.0, []

    def fire(name, points):
        nonlocal score
        score += float(points)
        fired.append(name)

    if "long_message" in s and len(text) >= s["long_message"].get("min_chars", 800):
        fire("long_message", s["long_message"].get("points", 2))
    if "very_long_message" in s and len(text) >= s["very_long_message"].get("min_chars", 3000):
        fire("very_long_message", s["very_long_message"].get("points", 2))
    if "code_block" in s and "```" in text:
        fire("code_block", s["code_block"].get("points", 2))
    if "many_questions" in s and text.count("?") >= s["many_questions"].get("min_count", 3):
        fire("many_questions", s["many_questions"].get("points", 1))
    if "long_conversation" in s and len(messages) >= s["long_conversation"].get("min_messages", 6):
        fire("long_conversation", s["long_conversation"].get("points", 1))
    normalized = normalize(text)
    if config.terms and any(p.search(normalized) for p in config.terms):
        fire("analysis_terms", (s.get("analysis_terms") or {}).get("points", 2))
    tier = next(t for t in config.tiers if t.max_score is None or score <= t.max_score)
    return tier, score, fired


# --- disponibilidade ------------------------------------------------------------

class Availability:
    def __init__(self):
        self._failures: dict = {}
        self._paused_until: dict = {}
        self._lock = threading.Lock()

    def record_failure(self, model: str, settings: dict, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            events = self._failures.setdefault(model, deque())
            events.append(now)
            while events and events[0] < now - settings["window_seconds"]:
                events.popleft()
            if len(events) >= settings["failure_threshold"]:
                self._paused_until[model] = now + settings["cooldown_seconds"]
                events.clear()
                logger.warning("model_router: %s pausado por %ss após falhas recentes", model, settings["cooldown_seconds"])

    def available(self, model: str, now: float | None = None) -> bool:
        with self._lock:
            return self._paused_until.get(model, 0) <= (now or time.time())


# --- hook -------------------------------------------------------------------------

def _load_access_store(path: str):
    try:
        spec = importlib.util.spec_from_file_location("corporate_model_access_for_router", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.PolicyStore(module.POLICY_PATH)
    except Exception as err:
        logger.error("model_router: política de acesso indisponível (%s)", err)
        return None


def _registered_quota_policy():
    """A instância de QuotaPolicy registrada no LiteLLM (compartilha o consumo recente)."""
    import litellm
    for callback in list(getattr(litellm, "callbacks", []) or []):
        if type(callback).__name__ == "QuotaPolicy":
            return callback
    return None


class ModelRouter(CustomLogger):
    def __init__(self, store: PolicyStore, access_store=None, quota_lookup=_registered_quota_policy,
                 availability: Availability | None = None):
        super().__init__()
        self.store, self.access_store, self.quota_lookup = store, access_store, quota_lookup
        self.availability = availability or Availability()
        self.store.get()

    async def _quota_exclusion(self, user: str, groups: tuple, model: str):
        quota = self.quota_lookup()
        if quota is None:
            return "quota_unknown"
        config = quota.store.get()
        if config is None:
            return "quota_unknown"
        if model in config.unlimited_models:
            return None
        try:
            reason, _ = await quota.evaluate(config, user, groups, model)
        except Exception as err:
            logger.error("model_router: falha ao consultar cota de %s/%s (%s)", user, model, type(err).__name__)
            return "quota_unknown"
        return QUOTA_EXCLUSION.get(reason) if reason else None

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        config = self.store.get()
        auto_model = config.auto_model if config else "auto"
        if data.get("model") != auto_model:
            return data
        role = getattr(user_api_key_dict, "user_role", None)
        is_admin = getattr(role, "value", role) == ADMIN_ROLE
        user = getattr(user_api_key_dict, "end_user_id", None)
        user = user.strip().lower() if isinstance(user, str) and user.strip() else None
        access = self.access_store.get() if self.access_store is not None else None
        allowed = None if is_admin else (access.allowed_models(user) if access is not None and user else frozenset())
        groups = tuple(access.groups_for(user)) if access is not None and user else ()

        meta = data.setdefault("metadata", {}).setdefault("spend_logs_metadata", {})
        meta.setdefault("requested_model", auto_model)

        decision = {"excluded": {}}
        chosen = None
        if config is not None:
            tier, score, fired = complexity(config, data.get("messages") or [])
            decision.update({"tier": tier.name, "score": score, "signals": fired, "min_quality": tier.min_quality})
            candidates = []
            for model, (quality, cost) in config.models.items():
                if allowed is not None and model not in allowed:
                    decision["excluded"][model] = "permission"
                    continue
                if user is not None:
                    exclusion = await self._quota_exclusion(user, groups, model)
                    if exclusion:
                        decision["excluded"][model] = exclusion
                        continue
                if not self.availability.available(model):
                    decision["excluded"][model] = "unavailable"
                    continue
                candidates.append((model, quality, cost))
            meeting = [c for c in candidates if c[1] >= tier.min_quality]
            if meeting:
                chosen = min(meeting, key=lambda c: (c[2], -c[1]))[0]
                decision["rule"] = "cheapest_meeting_quality"
            elif candidates:
                chosen = max(candidates, key=lambda c: (c[1], -c[2]))[0]
                decision["rule"] = "best_available"

        if chosen is None:
            fallback = config.fallback_model if config else "local-ai"
            if allowed is None or fallback in allowed:
                chosen, decision["rule"] = fallback, "no_candidate_fallback"
            else:
                meta.update({"routing_reason": ROUTING_REASON, "auto_decision": decision, "blocked": True})
                meta.pop("effective_model", None)
                logger.warning("model_router: nenhum modelo disponível para %s — negando", user or "-")
                raise HTTPException(status_code=403, detail={"error": "MODEL_ACCESS_DENIED", "reason": "auto_no_candidate"})

        decision["chosen"] = chosen
        data["model"] = chosen
        meta.update({"effective_model": chosen, "routing_reason": ROUTING_REASON, "auto_decision": decision})
        logger.warning(
            "model_router: AUTO user=%s → %s (faixa=%s score=%s regra=%s excluídos=%s)",
            user or "-", chosen, decision.get("tier", "-"), decision.get("score", "-"), decision["rule"],
            ",".join(f"{m}:{r}" for m, r in decision["excluded"].items()) or "-",
        )
        return data

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        config = self.store.get()
        slo = kwargs.get("standard_logging_object") or {}
        model = slo.get("model_group") or kwargs.get("model")
        if config is not None and model in config.models:
            self.availability.record_failure(model, config.availability)


handler = ModelRouter(PolicyStore(POLICY_PATH), _load_access_store(ACCESS_MODULE_PATH))
