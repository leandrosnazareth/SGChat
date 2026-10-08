"""Testes unitários do controle de acesso a modelos (litellm/custom/model_access.py).

Executados dentro do container do gateway, onde estão LiteLLM, FastAPI e PyYAML:

    docker compose exec -T litellm python3 - < tests/unit/test_model_access.py

O módulo é carregado como o LiteLLM o carrega (pelo caminho do arquivo, sem
registrá-lo em sys.modules). Sem pytest: cada teste é uma função `test_*`;
o script termina com código ≠ 0 se algum falhar.
"""

import asyncio
import importlib.util
import logging
import os
import sys
import tempfile
import traceback
from types import SimpleNamespace

logging.disable(logging.CRITICAL)

MODULE_PATH = os.environ.get("MODEL_ACCESS_MODULE", "/app/custom/model_access.py")
os.environ["MODEL_ACCESS_POLICY_PATH"] = "/nao/existe.yaml"  # o handler global não interfere
_spec = importlib.util.spec_from_file_location("custom.model_access", MODULE_PATH)
ma = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ma)

BASE = """
version: 1
default_group: USER
groups:
  USER: {models: [local-ai, gemini]}
  FINANCE: {models: [local-ai]}
  EXTRA: {models: [modelo-x]}
users:
  bruno@corp.local: [FINANCE]
  multi@corp.local: [FINANCE, EXTRA]
"""


def write_policy(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


def hook_for(text: str | None):
    path = write_policy(text) if text is not None else "/nao/existe.yaml"
    return ma.ModelAccessControl(ma.PolicyStore(path)), path


def key_for(email=None, role="internal_user"):
    # Como o LiteLLM entrega a identidade: end_user_id no UserAPIKeyAuth.
    return SimpleNamespace(user_role=role, end_user_id=email)


def call(hook, model, email=None, role="internal_user"):
    data = {"model": model}
    key = key_for(email, role)
    try:
        asyncio.run(hook.async_pre_call_hook(key, None, data, "acompletion"))
        return "allowed"
    except ma.HTTPException as exc:
        assert exc.status_code == 403, exc.status_code
        assert exc.detail["error"] == "MODEL_ACCESS_DENIED", exc.detail
        return exc.detail["reason"]


# --- avaliação da política ---------------------------------------------------

def test_union_of_groups():
    p = ma.parse_policy(__import__("yaml").safe_load(BASE))
    assert p.allowed_models("multi@corp.local") == {"local-ai", "modelo-x"}


def test_default_group_for_unlisted_user():
    p = ma.parse_policy(__import__("yaml").safe_load(BASE))
    assert p.groups_for("novo@corp.local") == ("USER",)
    assert p.allowed_models("novo@corp.local") == {"local-ai", "gemini"}


def test_email_case_insensitive():
    hook, _ = hook_for(BASE)
    assert call(hook, "gemini", "BRUNO@Corp.Local") == "model_not_allowed"
    assert call(hook, "local-ai", "  Bruno@corp.local ") == "allowed"


# --- hook --------------------------------------------------------------------

def test_allowed_and_denied():
    hook, _ = hook_for(BASE)
    assert call(hook, "gemini", "ana@corp.local") == "allowed"       # padrão USER
    assert call(hook, "gemini", "bruno@corp.local") == "model_not_allowed"
    assert call(hook, "local-ai", "bruno@corp.local") == "allowed"


def test_model_outside_policy_denied():
    hook, _ = hook_for(BASE)
    assert call(hook, "gpt", "ana@corp.local") == "model_not_allowed"


def test_missing_identity_denied():
    hook, _ = hook_for(BASE)
    assert call(hook, "local-ai") == "missing_identity"
    assert call(hook, "local-ai", "   ") == "missing_identity"


def test_identity_ignores_request_headers():
    # A identidade vem só do end_user_id; um cabeçalho solto no corpo não conta.
    hook, _ = hook_for(BASE)
    key = key_for(None)
    data = {"model": "local-ai", "proxy_server_request": {"headers": {"x-corporate-user-email": "ana@corp.local"}}}
    try:
        asyncio.run(hook.async_pre_call_hook(key, None, data, "acompletion"))
        raise AssertionError("deveria negar sem end_user_id")
    except ma.HTTPException as exc:
        assert exc.detail["reason"] == "missing_identity"


def test_admin_key_bypasses_policy():
    hook, _ = hook_for(None)  # mesmo sem política
    assert call(hook, "gemini", role="proxy_admin") == "allowed"


# --- listagem filtrada -------------------------------------------------------

CATALOG = ("local-ai", "gemini", "modelo-x")


def listed(hook, email=None, role="internal_user"):
    return asyncio.run(hook.async_filter_listed_models(key_for(email, role), CATALOG))


def test_listing_partial_and_order_preserved():
    hook, _ = hook_for(BASE)
    assert listed(hook, "bruno@corp.local") == ["local-ai"]
    assert listed(hook, "ana@corp.local") == ["local-ai", "gemini"]          # padrão USER
    assert listed(hook, "multi@corp.local") == ["local-ai", "modelo-x"]       # união, ordem do catálogo


def test_listing_without_identity_is_empty():
    hook, _ = hook_for(BASE)
    assert listed(hook, None) == []
    assert listed(hook, "  ") == []


def test_listing_without_policy_is_empty():
    hook, _ = hook_for(None)
    assert listed(hook, "ana@corp.local") == []


def test_listing_admin_sees_all():
    hook, _ = hook_for(None)
    assert listed(hook, role="proxy_admin") == list(CATALOG)


# --- fail-closed -------------------------------------------------------------

def test_missing_policy_denies():
    hook, _ = hook_for(None)
    assert call(hook, "local-ai", "ana@corp.local") == "policy_unavailable"


def test_invalid_yaml_denies():
    hook, _ = hook_for("version: 1\ngroups: [quebrado")
    assert call(hook, "local-ai", "ana@corp.local") == "policy_unavailable"


def test_invalid_structures_rejected():
    import yaml
    bad = {
        "versão errada": BASE.replace("version: 1", "version: 2"),
        "grupo padrão inexistente": BASE.replace("default_group: USER", "default_group: NAO_EXISTE"),
        "usuário em grupo inexistente": BASE.replace("[FINANCE]\n", "[FANTASMA]\n", 1),
        "grupo sem modelos": BASE.replace("{models: [local-ai]}", "{models: []}"),
        "groups vazio": "version: 1\ndefault_group: USER\ngroups: {}\n",
    }
    for name, text in bad.items():
        try:
            ma.parse_policy(yaml.safe_load(text))
        except ma.PolicyError:
            continue
        raise AssertionError(f"política aceita indevidamente: {name}")


# --- recarga -----------------------------------------------------------------

def test_hot_reload_and_recovery():
    hook, path = hook_for(BASE)
    assert call(hook, "local-ai", "bruno@corp.local") == "allowed"
    with open(path, "w") as fh:  # remove local-ai do FINANCE
        fh.write(BASE.replace("FINANCE: {models: [local-ai]}", "FINANCE: {models: [modelo-x]}"))
    os.utime(path, (1, 1))
    assert call(hook, "local-ai", "bruno@corp.local") == "model_not_allowed"
    with open(path, "w") as fh:  # política quebrada → nega
        fh.write("::: não é yaml válido :::\n  - [")
    os.utime(path, (2, 2))
    assert call(hook, "local-ai", "bruno@corp.local") == "policy_unavailable"
    with open(path, "w") as fh:  # corrigida → volta a valer
        fh.write(BASE)
    os.utime(path, (3, 3))
    assert call(hook, "local-ai", "bruno@corp.local") == "allowed"


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
