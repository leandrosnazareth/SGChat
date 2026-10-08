"""Testes unitários das cotas e budgets (litellm/custom/quota_policy.py).

Executados dentro do container do gateway:

    docker compose exec -T litellm python3 - < tests/unit/test_quota_policy.py

Os módulos são carregados como o LiteLLM os carrega (pelo caminho, sem sys.modules).
A apuração do banco é simulada; nenhum teste acessa o banco ou chama modelos.
"""

import asyncio
import importlib.util
import logging
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import yaml

logging.disable(logging.CRITICAL)
os.environ["QUOTA_POLICY_PATH"] = "/nao/existe.yaml"
os.environ["MODEL_ACCESS_POLICY_PATH"] = "/nao/existe.yaml"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


qp = load("custom.quota_policy", "/app/custom/quota_policy.py")
ma = load("custom.model_access", "/app/custom/model_access.py")

ACCESS = """
version: 1
default_group: USER
groups:
  USER: {models: [local-ai, gemini]}
  FINANCE: {models: [local-ai]}
  SEM_LOCAL: {models: [gemini]}
users:
  bruno@corp.local: [FINANCE]
  zeca@corp.local: [SEM_LOCAL]
"""

QUOTAS = """
version: 1
fallback_model: local-ai
unlimited_models: [local-ai]
rules:
  - name: externo-por-usuario
    applies_to: all_users
    models: external
    period: monthly
    max_tokens: 1000
    max_cost_usd: 1.0
"""


def write(text):
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


class FakeDb:
    """Simula LiteLLM_SpendLogs: devolve valores fixos ou levanta erro."""

    def __init__(self, tokens=0, cost=0.0, error=None):
        self.tokens, self.cost, self.error, self.calls = tokens, cost, error, []

    async def total(self, start, end, user, models, excluded):
        self.calls.append((start, end, user, models, excluded))
        if self.error:
            raise self.error
        return self.tokens, self.cost


def make_hook(quotas=QUOTAS, access=ACCESS, db=None):
    store = qp.QuotaStore(write(quotas)) if quotas is not None else qp.QuotaStore("/nao/existe.yaml")
    access_store = ma.PolicyStore(write(access)) if access is not None else None
    return qp.QuotaPolicy(store, access_store, db or FakeDb())


def pre_call(hook, model, user="ana@corp.local", role="internal_user"):
    data = {"model": model}
    key = SimpleNamespace(user_role=role, end_user_id=user)
    try:
        asyncio.run(hook.async_pre_call_hook(key, None, data, "acompletion"))
        return data, None
    except qp.HTTPException as exc:
        return data, exc


def meta(data):
    return data.get("metadata", {}).get("spend_logs_metadata", {})


# --- validação da política --------------------------------------------------

def test_valid_policy_parses():
    cfg = qp.parse_quotas(yaml.safe_load(QUOTAS))
    assert cfg.fallback_model == "local-ai" and len(cfg.rules) == 1
    assert cfg.rules[0].models is None and cfg.rules[0].max_tokens == 1000


def test_invalid_policies_rejected():
    base = yaml.safe_load(QUOTAS)

    def variant(**changes):
        d = yaml.safe_load(QUOTAS)
        rule_changes = changes.pop("rule", {})
        d.update(changes)
        d["rules"][0].update(rule_changes)
        return d

    cases = {
        "versão": variant(version=2),
        "fallback fora dos ilimitados": variant(fallback_model="gemini"),
        "applies_to inválido": variant(rule={"applies_to": "time:x"}),
        "models com ilimitado": variant(rule={"models": ["local-ai"]}),
        "período inválido": variant(rule={"period": "yearly"}),
        "sem limites": variant(rule={"max_tokens": None, "max_cost_usd": None}),
        "limite negativo": variant(rule={"max_tokens": -1}),
        "ação inválida": variant(rule={"action": "IGNORE"}),
        "nome repetido": {**base, "rules": base["rules"] * 2},
    }
    for name, raw in cases.items():
        try:
            qp.parse_quotas(raw)
        except qp.QuotaPolicyError:
            continue
        raise AssertionError(f"política aceita indevidamente: {name}")


# --- períodos (fuso America/Sao_Paulo, UTC-3) --------------------------------

def test_period_start():
    now = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)  # quarta, 12:00 em São Paulo
    tz = qp.ZoneInfo("America/Sao_Paulo")
    assert qp.period_start("daily", now, tz) == datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)
    assert qp.period_start("weekly", now, tz) == datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)
    assert qp.period_start("monthly", now, tz) == datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
    early = datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc)  # ainda 30/09 em São Paulo
    assert qp.period_start("monthly", early, tz) == datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)


# --- regras aplicáveis ------------------------------------------------------

def test_applicable_rules():
    cfg = qp.parse_quotas(yaml.safe_load("""
version: 1
fallback_model: local-ai
unlimited_models: [local-ai]
rules:
  - {name: u, applies_to: "user:Ana@Corp.Local", models: [gemini], period: daily, max_tokens: 1}
  - {name: g, applies_to: "group:FINANCE", models: external, period: daily, max_tokens: 1}
  - {name: a, applies_to: all_users, models: external, period: daily, max_tokens: 1}
  - {name: x, applies_to: global, models: [outro], period: daily, max_cost_usd: 1}
"""))
    names = lambda user, groups, model: sorted(r.name for r in cfg.applicable(user, groups, model))
    assert names("ana@corp.local", ("USER",), "gemini") == ["a", "u"]
    assert names("bruno@corp.local", ("FINANCE",), "gemini") == ["a", "g"]
    assert names("bruno@corp.local", ("FINANCE",), "outro") == ["a", "g", "x"]
    assert names("ana@corp.local", ("USER",), "local-ai") == []


# --- decisões no hook -------------------------------------------------------

def test_within_quota_is_direct():
    hook = make_hook(db=FakeDb(tokens=10, cost=0.01))
    data, exc = pre_call(hook, "gemini")
    assert exc is None and data["model"] == "gemini"
    assert meta(data)["routing_reason"] == "DIRECT" and meta(data)["groups"] == ["USER"]


def test_token_quota_fallback():
    hook = make_hook(db=FakeDb(tokens=1000, cost=0.0))
    data, exc = pre_call(hook, "gemini")
    assert exc is None and data["model"] == "local-ai"
    m = meta(data)
    assert (m["requested_model"], m["effective_model"], m["routing_reason"], m["quota_rule"]) == (
        "gemini", "local-ai", "TOKEN_QUOTA_EXCEEDED", "externo-por-usuario")


def test_budget_fallback_and_token_priority():
    data, _ = pre_call(make_hook(db=FakeDb(tokens=0, cost=1.0)), "gemini")
    assert meta(data)["routing_reason"] == "BUDGET_EXCEEDED" and data["model"] == "local-ai"
    data, _ = pre_call(make_hook(db=FakeDb(tokens=5000, cost=9.0)), "gemini")
    assert meta(data)["routing_reason"] == "TOKEN_QUOTA_EXCEEDED"


def test_rule_action_block():
    hook = make_hook(quotas=QUOTAS.replace("max_cost_usd: 1.0", "max_cost_usd: 1.0\n    action: BLOCK"),
                     db=FakeDb(tokens=1000))
    data, exc = pre_call(hook, "gemini")
    assert exc is not None and exc.status_code == 429
    assert exc.detail["error"] == "TOKEN_QUOTA_EXCEEDED" and data["model"] == "gemini"


def test_zero_limit_is_exhausted():
    hook = make_hook(quotas=QUOTAS.replace("max_tokens: 1000", "max_tokens: 0"), db=FakeDb(tokens=0))
    data, _ = pre_call(hook, "gemini")
    assert data["model"] == "local-ai"


def test_fallback_not_allowed_blocks():
    hook = make_hook(db=FakeDb(tokens=1000))
    data, exc = pre_call(hook, "gemini", user="zeca@corp.local")   # grupo sem local-ai
    assert exc is not None and exc.status_code == 429 and data["model"] == "gemini"


def test_unlimited_model_never_limited():
    db = FakeDb(tokens=10**9, cost=10**6)
    data, exc = pre_call(make_hook(db=db), "local-ai")
    assert exc is None and data["model"] == "local-ai" and db.calls == []


def test_admin_and_missing_identity_skip():
    db = FakeDb(tokens=10**9)
    for kwargs in ({"role": "proxy_admin"}, {"user": None}, {"user": "  "}):
        data, exc = pre_call(make_hook(db=db), "gemini", **kwargs)
        assert exc is None and data["model"] == "gemini"
    assert db.calls == []


# --- fail-closed ------------------------------------------------------------

def test_invalid_quota_policy_fails_closed():
    hook = make_hook(quotas="version: 1\nrules: [quebrado")
    data, exc = pre_call(hook, "gemini")
    assert exc is None and data["model"] == "local-ai"
    assert meta(data)["routing_reason"] == "QUOTA_CHECK_UNAVAILABLE"
    data, exc = pre_call(hook, "local-ai")          # IA Local segue normal
    assert exc is None and data["model"] == "local-ai"


def test_db_error_fails_closed():
    hook = make_hook(db=FakeDb(error=RuntimeError("banco fora")))
    data, exc = pre_call(hook, "gemini")
    assert data["model"] == "local-ai" and meta(data)["routing_reason"] == "QUOTA_CHECK_UNAVAILABLE"


# --- apuração: banco + memória ----------------------------------------------

def test_memory_window_counts_recent_usage():
    hook = make_hook(db=FakeDb(tokens=0))
    hook.memory.add(time.time(), "Ana@corp.local", "gemini", 600, 0.1)
    hook.memory.add(time.time(), "ana@corp.local", "gemini", 500, 0.1)
    hook.memory.add(time.time(), "outra@corp.local", "gemini", 5000, 5)   # outro usuário
    hook.memory.add(time.time(), "ana@corp.local", "local-ai", 5000, 0)    # ilimitado
    data, _ = pre_call(hook, "gemini")
    assert meta(data)["routing_reason"] == "TOKEN_QUOTA_EXCEEDED"       # 600 + 500 ≥ 1000


def test_memory_ignores_events_before_window():
    hook = make_hook(db=FakeDb(tokens=0))
    hook.memory.add(time.time() - (qp.SETTLE_SECONDS + 10), "ana@corp.local", "gemini", 5000, 5)
    data, _ = pre_call(hook, "gemini")
    assert meta(data)["routing_reason"] == "DIRECT"   # já está no banco (simulado = 0)


def test_global_rule_counts_everyone():
    quotas = QUOTAS.replace("applies_to: all_users", "applies_to: global")
    hook = make_hook(quotas=quotas, db=FakeDb(tokens=0))
    hook.memory.add(time.time(), "outra@corp.local", "gemini", 1500, 0)
    data, _ = pre_call(hook, "gemini")
    assert meta(data)["routing_reason"] == "TOKEN_QUOTA_EXCEEDED"
    assert hook.db.calls == [] or all(call[2] is None for call in hook.db.calls)


def test_db_query_scope():
    db = FakeDb(tokens=0)
    hook = make_hook(db=db)
    now = datetime.now(timezone.utc)
    cfg = hook.store.get()
    asyncio.run(hook.usage(cfg.rules[0], cfg, "ana@corp.local", now))
    start, end, user, models, excluded = db.calls[0]
    assert user == "ana@corp.local" and models is None and excluded == frozenset({"local-ai"})
    assert abs((now - end).total_seconds() - qp.SETTLE_SECONDS) < 1 and start <= end


def test_success_event_feeds_memory():
    hook = make_hook(db=FakeDb(tokens=0))
    kwargs = {"standard_logging_object": {"model_group": "gemini", "end_user": "ana@corp.local",
                                          "total_tokens": 1200, "response_cost": 0.0}}
    asyncio.run(hook.async_log_success_event(kwargs, None, datetime.now(timezone.utc), datetime.now(timezone.utc)))
    data, _ = pre_call(hook, "gemini")
    assert meta(data)["routing_reason"] == "TOKEN_QUOTA_EXCEEDED"


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
