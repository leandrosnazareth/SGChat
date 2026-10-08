"""Testes unitários do ModelRouter (litellm/custom/model_router.py) — modelo "auto".

Executados dentro do container do gateway:

    docker compose exec -T litellm python3 - < tests/unit/test_model_router.py

Política de acesso real (formato), cota simulada; nenhuma chamada de rede ou a modelos.
"""

import asyncio
import importlib.util
import logging
import os
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace

import yaml

logging.disable(logging.CRITICAL)
os.environ["ROUTING_POLICY_PATH"] = "/nao/existe.yaml"
os.environ["MODEL_ACCESS_POLICY_PATH"] = "/nao/existe.yaml"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mr = load("custom.model_router", "/app/custom/model_router.py")
ma = load("custom.model_access", "/app/custom/model_access.py")
REAL_ROUTING = open("/app/policies/routing.yaml").read()
ACCESS = """
version: 1
default_group: USER
groups:
  USER: {models: [auto, local-ai, gemini]}
  FINANCE: {models: [auto, local-ai]}
  SO_AUTO: {models: [auto]}
users:
  bruno@corp.local: [FINANCE]
  zeca@corp.local: [SO_AUTO]
"""


def write(text):
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


class FakeQuota:
    """Imita a QuotaPolicy registrada: store.get() e evaluate()."""

    def __init__(self, exhausted=None, unlimited=("local-ai",), error=None):
        self.exhausted, self.error, self.calls = dict(exhausted or {}), error, []
        cfg = SimpleNamespace(unlimited_models=frozenset(unlimited))
        self.store = SimpleNamespace(get=lambda: cfg)

    async def evaluate(self, config, user, groups, model):
        self.calls.append((user, groups, model))
        if self.error:
            raise self.error
        reason = self.exhausted.get(model)
        return (reason, None) if reason else (None, None)


def make_router(routing=REAL_ROUTING, quota=None, quota_missing=False):
    store = mr.PolicyStore(write(routing)) if routing is not None else mr.PolicyStore("/nao/existe.yaml")
    q = quota or FakeQuota()
    return mr.ModelRouter(store, ma.PolicyStore(write(ACCESS)), lambda: None if quota_missing else q)


SIMPLE = [{"role": "user", "content": "Quanto é 15% de 200?"}]
COMPLEX = [{"role": "user", "content": "Faça uma análise comparativa detalhada entre microsserviços e monolito, com trade-offs."}]


def route(router, messages, user="ana@corp.local", model="auto", role="internal_user", meta=None):
    data = {"model": model, "messages": messages}
    if meta:
        data["metadata"] = {"spend_logs_metadata": dict(meta)}
    key = SimpleNamespace(user_role=role, end_user_id=user)
    try:
        asyncio.run(router.async_pre_call_hook(key, None, data, "acompletion"))
        return data, None
    except mr.HTTPException as exc:
        return data, exc


def decision(data):
    return data["metadata"]["spend_logs_metadata"]["auto_decision"]


# --- política e complexidade ------------------------------------------------------

def test_policy_validation():
    cfg = mr.parse_routing(yaml.safe_load(REAL_ROUTING))
    assert cfg.auto_model == "auto" and set(cfg.models) == {"local-ai", "gemini"}
    bad = [
        REAL_ROUTING.replace("version: 1", "version: 9"),
        REAL_ROUTING.replace("gemini:   {quality: 4, cost: 1}", "auto: {quality: 4, cost: 1}"),
        REAL_ROUTING.replace("{name: complex,               min_quality: 4}", "{name: complex, max_score: 9, min_quality: 4}"),
        REAL_ROUTING.replace("{name: medium,  max_score: 3", "{name: medium,  max_score: 1"),
        REAL_ROUTING.replace("{quality: 2, cost: 0}", "{quality: -1, cost: 0}"),
    ]
    for text in bad:
        try:
            mr.parse_routing(yaml.safe_load(text))
        except mr.RoutingPolicyError:
            continue
        raise AssertionError("política aceita indevidamente")


def test_complexity_signals_and_tiers():
    cfg = mr.parse_routing(yaml.safe_load(REAL_ROUTING))
    tier, score, fired = mr.complexity(cfg, SIMPLE)
    assert (tier.name, score, fired) == ("simple", 0.0, [])
    tier, score, fired = mr.complexity(cfg, COMPLEX)
    assert fired == ["analysis_terms"] and tier.name == "medium"
    long_text = "Compare as opções. " + "x " * 450 + "\n```py\nprint(1)\n```"
    tier, score, fired = mr.complexity(cfg, [{"role": "user", "content": long_text}])
    assert set(fired) == {"long_message", "code_block", "analysis_terms"} and tier.name == "complex"
    many = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}] * 3
    many[-2] = {"role": "user", "content": "Por quê? Como? Quando? Explique detalhadamente."}
    tier, score, fired = mr.complexity(cfg, many)
    assert {"many_questions", "long_conversation", "analysis_terms"} <= set(fired) and tier.name == "complex"


def test_only_last_user_message_and_accents():
    cfg = mr.parse_routing(yaml.safe_load(REAL_ROUTING))
    msgs = [{"role": "user", "content": "Análise detalhada, por favor."}, {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "Obrigado!"}]
    assert mr.complexity(cfg, msgs)[2] == []
    assert mr.complexity(cfg, [{"role": "user", "content": "ANÁLISE"}])[2] == ["analysis_terms"]


# --- escolha -----------------------------------------------------------------------

COMPLEX_HARD = [{"role": "user", "content": "Compare detalhadamente três arquiteturas. " + "contexto " * 120}]


def test_simple_goes_local_cheapest():
    data, exc = route(make_router(), SIMPLE)
    d = decision(data)
    assert exc is None and data["model"] == "local-ai" and d["tier"] == "simple" and d["rule"] == "cheapest_meeting_quality"
    m = data["metadata"]["spend_logs_metadata"]
    assert (m["requested_model"], m["effective_model"], m["routing_reason"]) == ("auto", "local-ai", "AUTO")


def test_complex_goes_external_when_allowed():
    data, _ = route(make_router(), COMPLEX_HARD)
    assert data["model"] == "gemini" and decision(data)["tier"] == "complex" and decision(data)["excluded"] == {}


def test_permission_excludes_external():
    data, _ = route(make_router(), COMPLEX_HARD, user="bruno@corp.local")
    d = decision(data)
    assert data["model"] == "local-ai" and d["excluded"] == {"gemini": "permission"} and d["rule"] == "best_available"


def test_quota_and_budget_exclusions():
    for reason, label in (("TOKEN_QUOTA_EXCEEDED", "quota"), ("BUDGET_EXCEEDED", "budget")):
        quota = FakeQuota(exhausted={"gemini": reason})
        data, _ = route(make_router(quota=quota), COMPLEX_HARD)
        assert data["model"] == "local-ai" and decision(data)["excluded"] == {"gemini": label}
        assert ("ana@corp.local", ("USER",), "gemini") in quota.calls


def test_quota_unknown_fails_closed():
    data, _ = route(make_router(quota_missing=True), COMPLEX_HARD)
    assert data["model"] == "local-ai" and decision(data)["excluded"]["gemini"] == "quota_unknown"
    data, _ = route(make_router(quota=FakeQuota(error=RuntimeError())), COMPLEX_HARD)
    assert data["model"] == "local-ai" and decision(data)["excluded"]["gemini"] == "quota_unknown"


def test_unavailable_model_excluded_then_recovers():
    router = make_router()
    settings = router.store.get().availability
    now = time.time()
    router.availability.record_failure("gemini", settings, now)
    router.availability.record_failure("gemini", settings, now)
    data, _ = route(router, COMPLEX_HARD)
    assert data["model"] == "local-ai" and decision(data)["excluded"] == {"gemini": "unavailable"}
    assert router.availability.available("gemini", now + settings["cooldown_seconds"] + 1)


def test_failure_event_feeds_availability():
    router = make_router()
    for _ in range(2):
        asyncio.run(router.async_log_failure_event({"standard_logging_object": {"model_group": "gemini"}}, None, None, None))
    assert not router.availability.available("gemini")


def test_no_candidate_fallback_or_403():
    data, exc = route(make_router(), COMPLEX_HARD, user="zeca@corp.local")
    assert exc is not None and exc.status_code == 403 and exc.detail["error"] == "MODEL_ACCESS_DENIED"
    policy = REAL_ROUTING.replace("  local-ai: {quality: 2, cost: 0}\n", "")
    data, exc = route(make_router(routing=policy), COMPLEX_HARD, user="bruno@corp.local")
    assert exc is None and data["model"] == "local-ai" and decision(data)["rule"] == "no_candidate_fallback"


def test_only_acts_on_auto_and_respects_security():
    router = make_router()
    data, _ = route(router, COMPLEX_HARD, model="gemini")
    assert data["model"] == "gemini" and "metadata" not in data
    # segurança já redirecionou: data["model"] == local-ai com requested_model=auto
    data, _ = route(router, COMPLEX_HARD, model="local-ai",
                    meta={"requested_model": "auto", "routing_reason": "SECURITY_POLICY"})
    m = data["metadata"]["spend_logs_metadata"]
    assert data["model"] == "local-ai" and m["routing_reason"] == "SECURITY_POLICY" and "auto_decision" not in m


def test_admin_has_all_candidates():
    data, _ = route(make_router(), COMPLEX_HARD, user=None, role="proxy_admin")
    assert data["model"] == "gemini"


def test_invalid_policy_uses_fallback():
    data, exc = route(make_router(routing="version: 1\nmodels: [quebrado"), COMPLEX_HARD)
    assert exc is None and data["model"] == "local-ai" and decision(data)["rule"] == "no_candidate_fallback"


def test_metadata_has_no_text():
    secret_text = "Compare detalhadamente o projeto Fênix com o Órion " + "z " * 400
    data, _ = route(make_router(), [{"role": "user", "content": secret_text}])
    assert "Fênix" not in repr(data["metadata"]) and "Órion" not in repr(data["metadata"])


def test_policy_reload_changes_decision():
    path = write(REAL_ROUTING)
    router = mr.ModelRouter(mr.PolicyStore(path), ma.PolicyStore(write(ACCESS)), lambda: FakeQuota())
    data, _ = route(router, COMPLEX)          # medium → local-ai
    assert data["model"] == "local-ai" and decision(data)["tier"] == "medium"
    with open(path, "w") as fh:
        fh.write(REAL_ROUTING.replace("{name: medium,  max_score: 3, min_quality: 2}", "{name: medium,  max_score: 3, min_quality: 4}"))
    os.utime(path, (1, 1))
    data, _ = route(router, COMPLEX)          # medium agora exige qualidade 4 → gemini
    assert data["model"] == "gemini"


if __name__ == "__main__" or True:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✔ {name}")
        except Exception:
            failed += 1
            print(f"  ✘ {name}")
            traceback.print_exc()
    print(f"\nResultado: {len(tests) - failed} ok, {failed} falha(s)")
    sys.exit(1 if failed else 0)
