"""Testes unitários dos metadados de auditoria (litellm/custom/audit_metadata.py) e da redação
de conteúdo RESTRICTED (litellm/custom/security_router.py).

Executados dentro do container do gateway:

    docker compose exec -T litellm python3 - < tests/unit/test_audit_metadata.py

Sem rede: só os hooks e as funções puras.
"""

import asyncio
import importlib.util
import logging
import os
import sys
import traceback
from types import SimpleNamespace

logging.disable(logging.CRITICAL)
os.environ["SECURITY_POLICY_PATH"] = "/nao/existe.yaml"
os.environ["MODEL_ACCESS_POLICY_PATH"] = "/nao/existe.yaml"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


am = load("custom.audit_metadata", "/app/custom/audit_metadata.py")
sr = load("custom.security_router", "/app/custom/security_router.py")
KEY = SimpleNamespace(user_role="internal_user", end_user_id="ana@corp.local")


def run(hook, data):
    return asyncio.run(hook.async_pre_call_hook(KEY, None, data, "acompletion"))


def request(**metadata):
    return {"model": "gemini", "messages": [{"role": "user", "content": "oi"}], "metadata": dict(metadata)}


# --- sanitize ------------------------------------------------------------------------

def test_client_steering_keys_removed():
    data = request(tags=["x"], mask_input=True, mask_output=True, trace_name="x", trace_metadata={"a": 1},
                   trace_user_id="outro", existing_trace_id="t", generation_name="g", generation_id="i",
                   parent_observation_id="p", debug_langfuse=True, turn_off_message_logging=True,
                   langfuse_masking_function="f", session_id="conv-1", user_api_key_alias="librechat-portal")
    data.update({"turn_off_message_logging": True, "langfuse_host": "https://evil.example",
                 "langfuse_public_key": "pk", "langfuse_secret_key": "sk"})
    removed = am.sanitize(data)
    assert "turn_off_message_logging" not in data and not any(k.startswith("langfuse_") for k in data)
    assert set(data["metadata"]) == {"session_id", "user_api_key_alias"}
    assert "langfuse_host" in removed and "metadata.mask_input" in removed and len(removed) == 17


def test_redaction_headers_removed_others_kept():
    data = request(headers={"X-LiteLLM-Enable-Message-Redaction": "true", "litellm-disable-message-redaction": "1",
                            "litellm-enable-message-redaction": "1", "x-litellm-session-id": "conv-1"})
    am.sanitize(data)
    assert data["metadata"]["headers"] == {"x-litellm-session-id": "conv-1"}


def test_litellm_trace_id_kept_foreign_removed():
    data = request(trace_id="conv-1", session_id="conv-1")
    assert am.sanitize(data) == [] and data["metadata"]["trace_id"] == "conv-1"
    data = request(trace_id="trace-de-outro", session_id="conv-1")
    assert am.sanitize(data) == ["metadata.trace_id"]


def test_sanitize_without_metadata():
    data = {"model": "gemini", "messages": []}
    assert am.sanitize(data) == [] and data == {"model": "gemini", "messages": []}


# --- hooks ---------------------------------------------------------------------------

def test_start_links_trace_metadata_to_decisions():
    data = run(am.start, request(trace_name="x"))
    meta = data["metadata"]["spend_logs_metadata"]
    assert data["metadata"]["trace_metadata"] is meta and data["metadata"]["trace_name"] == am.TRACE_NAME
    assert meta == {"audit_version": am.AUDIT_VERSION}
    # um hook posterior (ex.: segurança) altera a decisão e depois bloqueia com exceção
    meta.update({"classification": "RESTRICTED", "routing_reason": "SECURITY_POLICY", "blocked": True})
    assert data["metadata"]["trace_metadata"]["blocked"] is True


def test_start_keeps_existing_decisions():
    data = request()
    data["metadata"]["spend_logs_metadata"] = {"requested_model": "gemini"}
    run(am.start, data)
    assert data["metadata"]["trace_metadata"] == {"requested_model": "gemini", "audit_version": 1}


def test_finish_tags():
    data = run(am.start, request())
    data["metadata"]["spend_logs_metadata"].update({"classification": "CONFIDENTIAL", "requested_model": "gemini",
                                                    "effective_model": "local-ai", "routing_reason": "SECURITY_POLICY"})
    data = run(am.finish, data)
    assert data["metadata"]["tags"] == ["classification:CONFIDENTIAL", "requested:gemini",
                                        "effective:local-ai", "routing:SECURITY_POLICY"]


def test_finish_tags_partial_and_redacted():
    assert am.audit_tags({}) == []
    assert am.audit_tags({"routing_reason": "AUTO", "content_redacted": True}) == ["routing:AUTO", "content:redacted"]


def test_finish_overrides_client_tags_even_without_start():
    data = run(am.finish, request(tags=["cliente"]))
    assert data["metadata"]["tags"] == []


# --- redação (Security Router) -------------------------------------------------------

def test_redact_messages_all_fields():
    data = {"messages": [{"role": "system", "content": "Você é útil."},
                         {"role": "user", "content": [{"type": "text", "text": "senha: Prod@2026!"}]},
                         {"role": "assistant"}],
            "prompt": "senha", "input": ["senha"]}
    sr.redact_messages(data, ["regex:credencial-declarada"])
    marker = "[REDACTED: RESTRICTED — regex:credencial-declarada]"
    assert [m.get("content") for m in data["messages"]] == [marker, marker, None]
    assert data["prompt"] == marker and data["input"] == marker
    assert "Prod@2026" not in repr(data)


def test_redact_messages_without_reasons():
    data = {"messages": [{"role": "user", "content": "x"}]}
    sr.redact_messages(data, [])
    assert data["messages"][0]["content"] == "[REDACTED: RESTRICTED — policy]"


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
