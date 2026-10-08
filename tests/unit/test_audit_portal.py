"""Testes unitários do portal de auditoria (audit-portal/portal). LibreChat, Langfuse e a trilha
são simulados; nenhuma rede nem banco.

Executados dentro do container do portal:

    docker compose exec -T audit-portal python3 - < tests/unit/test_audit_portal.py
"""

import json
import logging
import os
import sys
import warnings
import tempfile
import time
import traceback

import httpx

sys.path.insert(0, "/app")
os.environ.pop("AUDIT_SESSION_SECRET", None)  # não criar o app de produção ao importar
warnings.filterwarnings("ignore", category=DeprecationWarning)

from fastapi.testclient import TestClient  # noqa: E402

from portal import main  # noqa: E402
from portal.policy import FileStore, PolicyError, parse_groups, parse_policy, resolve  # noqa: E402
from portal.registry import InvalidFilter, RegistryUnavailable, build_filters, clean_filters, summarize  # noqa: E402
from portal.session import SessionSigner  # noqa: E402
from portal.trail import TrailUnavailable  # noqa: E402

logging.disable(logging.CRITICAL)

POLICY = """
version: 1
roles:
  AUDITOR: [SEARCH_CONVERSATIONS]
  MASTER_AUDITOR: [SEARCH_CONVERSATIONS, SEARCH_CONTENT, VIEW_CONVERSATION, EXPORT_CONVERSATION, VIEW_ACCESS_LOG]
reason_required: [VIEW_CONVERSATION, EXPORT_CONVERSATION]
reason_min_chars: 10
session_minutes: 30
max_results: 50
"""
GROUPS = """
version: 1
default_group: USER
users:
  carla@corp.local: [AUDITOR]
  Diego@Corp.local: [MASTER_AUDITOR]
  edu@corp.local: [ADMIN]
  dupla@corp.local: [AUDITOR, FINANCE]
"""
PASSWORDS = {"carla@corp.local": "s1", "diego@corp.local": "s2", "edu@corp.local": "s3"}
OBS = {"traceId": "t1", "startTime": "2026-10-07T10:00:00.123Z", "userId": "ana@corp.local", "sessionId": "conv-1",
       "level": "DEFAULT", "totalUsage": 50, "totalCost": 0.0001, "latency": 1.2,
       "metadata": {"classification": "CONFIDENTIAL", "requested_model": "gemini", "effective_model": "local-ai",
                    "routing_reason": "SECURITY_POLICY", "security_reasons": ["entity:cliente:XPTO"]},
       "input": json.dumps({"messages": [{"role": "user", "content": "<script>alert(1)</script> contrato XPTO"}]}),
       "output": json.dumps({"content": "Resposta sobre o contrato", "role": "assistant"})}


def write(text):
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


class FakeTrail:
    def __init__(self):
        self.events, self.fail, self.log = [], False, None

    async def append(self, action, outcome, actor, roles=(), channel="portal", targets=(), conversation=None,
                     trace=None, query=None, reason=None, count=None, ip=None):
        if self.fail:
            raise TrailUnavailable("banco fora")
        self.events.append(dict(action=action, outcome=outcome, actor=actor, roles=tuple(roles), targets=sorted(set(targets)),
                                conversation=conversation, trace=trace, query=query or {}, reason=reason, count=count))
        if self.log is not None:
            self.log.append(("trail", action))
        return len(self.events), f"hash{len(self.events)}"

    async def recent(self, limit=100):
        return [{"id": 1, "action": "LOGIN"}]

    async def verify(self):
        return {"total": 1, "first_invalid_id": None, "head_hash": "h", "intact": True}

    async def ping(self):
        return not self.fail


class FakeRegistry:
    def __init__(self):
        self.searches, self.log, self.fail = [], None, False

    async def search(self, filters, limit):
        if self.fail:
            raise RegistryUnavailable("fora")
        self.searches.append(filters)
        return [summarize(OBS)]

    async def calls(self, conversation=None, trace=None, limit=200):
        if self.log is not None:
            self.log.append(("registry", conversation or trace))
        from portal.registry import with_content
        return [with_content(OBS)] if (conversation == "conv-1" or trace == "t1") else []


def librechat(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/logout"):
        return httpx.Response(200)
    body = json.loads(request.content)
    if body["email"] == "limitado@corp.local":
        return httpx.Response(429, json={"message": "Too many login attempts"})
    if PASSWORDS.get(body["email"]) == body["password"]:
        return httpx.Response(200, json={"token": "x", "user": {"email": body["email"]}})
    return httpx.Response(404, json={"message": "Email or password is incorrect"})


def make(policy=POLICY, groups=GROUPS):
    trail, registry = FakeTrail(), FakeRegistry()
    portal = main.Portal(FileStore(write(policy), parse_policy, "política"), FileStore(write(groups), parse_groups, "grupos"),
                         SessionSigner("x" * 40), trail, registry, "http://librechat:3080",
                         transport=httpx.MockTransport(librechat))
    client = TestClient(main.create_app(portal))
    return client, trail, registry


def login(client, email):
    return client.post("/api/login", json={"email": email, "password": PASSWORDS.get(email, "errada")})


# --- política e perfis -------------------------------------------------------------------

def test_policy_validation():
    parse_policy(__import__("yaml").safe_load(POLICY))
    for bad in ("version: 2\nroles: {A: [SEARCH_CONVERSATIONS]}", "version: 1\nroles: {}",
                "version: 1\nroles: {A: [LER_TUDO]}", "version: 1\nroles: {A: []}",
                "version: 1\nroles: {A: [SEARCH_CONVERSATIONS]}\nsession_minutes: 0",
                "version: 1\nroles: {A: [SEARCH_CONVERSATIONS]}\nreason_required: [X]"):
        try:
            parse_policy(__import__("yaml").safe_load(bad))
        except PolicyError:
            continue
        raise AssertionError(f"aceitou: {bad}")


def test_roles_least_privilege_and_admin_excluded():
    policy, groups = parse_policy(__import__("yaml").safe_load(POLICY)), parse_groups(__import__("yaml").safe_load(GROUPS))
    assert resolve(policy, groups, "carla@corp.local") == (("AUDITOR",), frozenset({"SEARCH_CONVERSATIONS"}))
    roles, perms = resolve(policy, groups, "DIEGO@corp.local")          # e-mail sem diferenciar maiúsculas
    assert roles == ("MASTER_AUDITOR",) and "VIEW_CONVERSATION" in perms
    assert resolve(policy, groups, "edu@corp.local") == ((), frozenset())        # ADMIN ≠ AUDITOR
    assert resolve(policy, groups, "qualquer@corp.local") == ((), frozenset())   # default USER
    assert resolve(policy, groups, "dupla@corp.local")[0] == ("AUDITOR",)
    assert resolve(None, groups, "diego@corp.local") == ((), frozenset())        # política inválida


def test_filestore_reload_and_fail_closed():
    path = write(POLICY)
    store = FileStore(path, parse_policy, "política")
    assert store.get().max_results == 50
    with open(path, "w") as fh:
        fh.write("version: 1\nroles: [quebrado")
    os.utime(path, (1, 1))
    assert store.get() is None
    assert FileStore("/nao/existe.yaml", parse_policy, "x").get() is None


# --- sessão ------------------------------------------------------------------------------

def test_session_sign_expire_tamper_revoke_csrf():
    signer = SessionSigner("y" * 40)
    token, claims = signer.issue("diego@corp.local", 30)
    assert signer.verify(token)["sub"] == "diego@corp.local"
    body, sig = token.split(".")
    forged = json.dumps({**claims, "sub": "outro@corp.local"}, separators=(",", ":"), sort_keys=True).encode()
    import base64
    assert signer.verify(base64.urlsafe_b64encode(forged).rstrip(b"=").decode() + "." + sig) is None
    assert SessionSigner("z" * 40).verify(token) is None
    assert signer.verify(token, now=time.time() + 31 * 60) is None
    assert signer.csrf_ok(claims, signer.csrf(claims)) and not signer.csrf_ok(claims, "x") and not signer.csrf_ok(claims, None)
    signer.revoke(claims)
    assert signer.verify(token) is None
    try:
        SessionSigner("curto")
    except ValueError:
        return
    raise AssertionError("aceitou segredo curto")


# --- filtros e linhas sem conteúdo ---------------------------------------------------------

def test_filters():
    f = clean_filters({"user": " ana@corp.local ", "classification": "confidential", "reason": "security_policy",
                       "blocked": "1", "external": "", "date_from": "2026-10-07", "text": "", "lixo": "x"})
    assert f == {"user": "ana@corp.local", "classification": "CONFIDENTIAL", "reason": "SECURITY_POLICY",
                 "blocked": True, "date_from": "2026-10-07"}
    for bad in ({"classification": "SECRETO"}, {"date_to": "ontem"}, {"user": "x" * 201}):
        try:
            clean_filters(bad)
        except InvalidFilter:
            continue
        raise AssertionError(f"aceitou {bad}")
    built = build_filters(f)
    assert {"type": "stringObject", "column": "metadata", "key": "blocked", "operator": "=", "value": "true"} in built
    assert {"type": "datetime", "column": "startTime", "operator": ">=", "value": "2026-10-07T00:00:00Z"} in built


def test_summary_never_has_content():
    row = summarize(OBS)
    assert "input" not in row and "output" not in row and "question" not in row and "XPTO" not in json.dumps(row).replace("entity:cliente:XPTO", "")
    assert row["requested_model"] == "gemini" and row["effective_model"] == "local-ai" and row["external"] is False


# --- rotas: login e perfis ---------------------------------------------------------------

def test_login_wrong_password_admin_and_auditor():
    client, trail, _ = make()
    assert login(client, "carla@corp.local").status_code == 200
    r = client.post("/api/login", json={"email": "carla@corp.local", "password": "errada"})
    assert r.status_code == 401
    assert login(client, "edu@corp.local").status_code == 403
    assert [(e["action"], e["outcome"], e["actor"]) for e in trail.events] == [
        ("LOGIN", "ALLOWED", "carla@corp.local"), ("LOGIN", "FAILED", "carla@corp.local"), ("LOGIN", "DENIED", "edu@corp.local")]


def test_login_rate_limited_is_not_wrong_password():
    client, trail, _ = make()
    r = client.post("/api/login", json={"email": "limitado@corp.local", "password": "x"})
    assert r.status_code == 429 and "aguarde" in r.json()["error"]
    assert trail.events[-1]["outcome"] == "FAILED" and trail.events[-1]["query"] == {"reason": "rate_limited"}


def test_no_session_401_and_html_redirect():
    client, trail, _ = make()
    assert client.get("/api/search").status_code == 401
    assert client.post("/api/conversations/conv-1", json={"reason": "x" * 20}).status_code == 401
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert trail.events == []


def test_invalid_policy_denies_everyone():
    client, trail, _ = make(policy="version: 1\nroles: [quebrado")
    assert login(client, "diego@corp.local").status_code == 403
    assert trail.events[-1]["outcome"] == "DENIED" and trail.events[-1]["query"] == {"policy": "invalid"}


# --- auditor: só metadados ---------------------------------------------------------------

def test_auditor_search_metadata_only_and_denials():
    client, trail, registry = make()
    login(client, "carla@corp.local")
    r = client.get("/api/search", params={"classification": "CONFIDENTIAL", "user": "ana@corp.local"})
    assert r.status_code == 200 and r.json()["count"] == 1
    assert "Resposta sobre o contrato" not in r.text and "<script>" not in r.text
    ev = trail.events[-1]
    assert (ev["action"], ev["outcome"], ev["targets"], ev["count"]) == ("SEARCH_CONVERSATIONS", "ALLOWED", ["ana@corp.local"], 1)
    assert client.get("/api/search", params={"text": "contrato"}).status_code == 403          # termo revela conteúdo
    assert trail.events[-1]["outcome"] == "DENIED" and trail.events[-1]["query"]["missing_permission"] == "SEARCH_CONTENT"
    assert client.post("/api/conversations/conv-1", json={"reason": "investigação 42"}).status_code == 403
    assert client.post("/api/conversations/conv-1/export", json={"reason": "investigação 42"}).status_code == 403
    assert client.get("/api/access-log").status_code == 403
    page = client.get("/conversations/conv-1")                                              # HTML: recusa ao abrir
    assert page.status_code == 403 and "Motivo" not in page.text
    denied = [e["action"] for e in trail.events if e["outcome"] == "DENIED"]
    assert denied == ["SEARCH_CONVERSATIONS", "VIEW_CONVERSATION", "EXPORT_CONVERSATION", "VIEW_ACCESS_LOG", "VIEW_CONVERSATION"]
    assert len(registry.searches) == 1                                                       # negado não consulta


# --- master: conteúdo com motivo, gravado antes de entregar -------------------------------

def test_master_view_requires_reason_and_records_before_delivery():
    client, trail, registry = make()
    login(client, "diego@corp.local")
    trail.log, registry.log = [], []
    r = client.post("/api/conversations/conv-1", json={"reason": "curto"})
    assert r.status_code == 400 and registry.log == [] and trail.log == []
    r = client.post("/api/conversations/conv-1", json={"reason": "Investigação do chamado 42"})
    assert r.status_code == 200 and r.json()["calls"][0]["question"].endswith("contrato XPTO")
    assert registry.log == [("registry", "conv-1")] and trail.log == [("trail", "VIEW_CONVERSATION")]
    ev = trail.events[-1]
    assert (ev["conversation"], ev["targets"], ev["reason"], ev["count"]) == ("conv-1", ["ana@corp.local"], "Investigação do chamado 42", 1)
    assert client.post("/api/conversations/nao-existe", json={"reason": "Investigação do chamado 42"}).status_code == 404


def test_trail_down_means_no_content():
    client, trail, _ = make()
    login(client, "diego@corp.local")
    trail.fail = True
    for r in (client.post("/api/conversations/conv-1", json={"reason": "Investigação do chamado 42"}),
              client.post("/api/conversations/conv-1/export", json={"reason": "Investigação do chamado 42"}),
              client.get("/api/search", params={"user": "ana@corp.local"})):
        assert r.status_code == 503 and "contrato" not in r.text and "ana@corp.local" not in r.text
    assert client.get("/health").status_code == 503


def test_export_and_access_log():
    client, trail, _ = make()
    login(client, "diego@corp.local")
    r = client.post("/api/traces/t1/export", json={"reason": "Pedido do jurídico, processo 7"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["exported_by"] == "diego@corp.local" and data["trace_id"] == "t1" and data["access_event_id"] == len(trail.events)
    assert trail.events[-1]["action"] == "EXPORT_CONVERSATION" and trail.events[-1]["trace"] == "t1"
    r = client.get("/api/access-log")
    assert r.status_code == 200 and r.json()["chain"]["intact"] and trail.events[-1]["action"] == "VIEW_ACCESS_LOG"


def test_permissions_rechecked_each_request():
    groups_path = write(GROUPS)
    client, trail, _ = make()
    portal = client.app.state.portal
    portal.groups_store = FileStore(groups_path, parse_groups, "grupos")
    login(client, "diego@corp.local")
    assert client.get("/api/access-log").status_code == 200
    with open(groups_path, "w") as fh:
        fh.write(GROUPS.replace("Diego@Corp.local: [MASTER_AUDITOR]", "Diego@Corp.local: [ADMIN]"))
    os.utime(groups_path, (2, 2))
    assert client.get("/api/access-log").status_code == 403


# --- HTML: escape, CSRF, cabeçalhos --------------------------------------------------------

def test_html_escapes_content_and_requires_csrf():
    client, trail, _ = make()
    login(client, "diego@corp.local")
    page = client.get("/conversations/conv-1")
    assert page.status_code == 200 and "contrato" not in page.text                     # formulário de motivo, sem conteúdo
    csrf = page.text.split('name="csrf" value="')[1].split('"')[0]
    r = client.post("/conversations/conv-1", data={"reason": "Investigação do chamado 42"})
    assert r.status_code == 403 and "contrato" not in r.text
    r = client.post("/conversations/conv-1", data={"reason": "Investigação do chamado 42", "csrf": csrf})
    assert r.status_code == 200 and "&lt;script&gt;alert(1)&lt;/script&gt; contrato XPTO" in r.text and "<script>" not in r.text
    for header, value in (("cache-control", "no-store"), ("x-frame-options", "DENY")):
        assert r.headers[header] == value
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_logout_revokes_session():
    client, trail, _ = make()
    login(client, "carla@corp.local")
    cookie = client.cookies.get("audit_session")
    assert client.post("/api/logout").status_code == 200
    client.cookies.set("audit_session", cookie)
    assert client.get("/api/search").status_code == 401
    assert trail.events[-1]["action"] == "LOGOUT"


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
