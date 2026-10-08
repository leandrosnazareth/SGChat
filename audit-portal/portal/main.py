"""Portal de auditoria — Corporate AI Platform (Fase 9). Guia: docs/audit.md

Único caminho dos auditores até o registro das conversas (Langfuse):
- login com a conta do portal de chat (validada pelo LibreChat); perfil = grupos AUDITOR /
  MASTER_AUDITOR de model-access.yaml; permissões em policy.yaml (menor privilégio);
- AUDITOR: busca por metadados, sem conteúdo; MASTER_AUDITOR: também busca por termo, abre e
  exporta conversas (com motivo) e consulta a trilha;
- todo acesso (e toda negação) vira evento AUDIT_ACCESS na trilha imutável (trail.py), gravado
  ANTES de entregar o resultado: sem trilha, sem dados (fail-closed).

Páginas HTML (sem JavaScript) e API JSON (/api/...) com a mesma lógica.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .policy import FileStore, parse_groups, parse_policy, resolve
from .registry import InvalidFilter, Registry, RegistryUnavailable, clean_filters
from .session import SessionSigner
from .trail import Trail, TrailUnavailable

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("audit_portal")

COOKIE = "audit_session"
HERE = os.path.dirname(os.path.abspath(__file__))
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class Denied(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class Portal:
    """Estado e regras do portal (separado das rotas para os testes unitários)."""

    def __init__(self, policy_store, groups_store, signer, trail, registry, librechat_url: str, transport=None):
        self.policy_store, self.groups_store = policy_store, groups_store
        self.signer, self.trail, self.registry = signer, trail, registry
        self.librechat_url, self.transport = librechat_url.rstrip("/"), transport

    # --- identidade -----------------------------------------------------------------
    def who(self, request: Request) -> dict | None:
        claims = self.signer.verify(request.cookies.get(COOKIE))
        if claims is None:
            return None
        roles, perms = resolve(self.policy_store.get(), self.groups_store.get(), claims["sub"])
        return {"email": claims["sub"], "roles": roles, "perms": perms, "claims": claims,
                "csrf": self.signer.csrf(claims)}

    async def check_password(self, email: str, password: str, ip: str) -> bool | None:
        """Valida no LibreChat e encerra a sessão aberta lá (só a validação interessa).
        None = o LibreChat recusou por excesso de tentativas (limite por IP)."""
        try:
            async with httpx.AsyncClient(timeout=15, transport=self.transport) as client:
                resp = await client.post(f"{self.librechat_url}/api/auth/login", json={"email": email, "password": password},
                                         headers={"X-Forwarded-For": ip})
                if resp.status_code == 429:
                    return None
                if resp.status_code != 200:
                    return False
                data = resp.json()
                if str((data.get("user") or {}).get("email", "")).lower() != email.lower():
                    return False
                try:
                    await client.post(f"{self.librechat_url}/api/auth/logout",
                                      headers={"Authorization": f"Bearer {data.get('token', '')}"},
                                      cookies={"refreshToken": resp.cookies.get("refreshToken", "")})
                except httpx.HTTPError:
                    pass
                return True
        except (httpx.HTTPError, ValueError) as err:
            logger.error("login: LibreChat indisponível (%s)", type(err).__name__)
            raise Denied(503, "Serviço de autenticação indisponível.") from err

    async def login(self, email: str, password: str, ip: str) -> tuple:
        email = (email or "").strip().lower()
        if not email or not password:
            raise Denied(400, "Informe e-mail e senha.")
        valid = await self.check_password(email, password, ip)
        if valid is None:
            await self._record("LOGIN", "FAILED", email, (), ip=ip, query={"reason": "rate_limited"})
            raise Denied(429, "Muitas tentativas de login a partir deste endereço; aguarde alguns minutos.")
        if not valid:
            await self._record("LOGIN", "FAILED", email, (), ip=ip)
            raise Denied(401, "E-mail ou senha inválidos.")
        policy = self.policy_store.get()
        roles, perms = resolve(policy, self.groups_store.get(), email)
        if not perms:
            await self._record("LOGIN", "DENIED", email, roles, ip=ip,
                               query={"policy": "invalid"} if policy is None else {})
            raise Denied(403, "Acesso restrito a auditores (perfis AUDITOR e MASTER_AUDITOR).")
        token, claims = self.signer.issue(email, policy.session_minutes)
        await self.trail_or_503("LOGIN", "ALLOWED", email, roles, ip=ip)
        return token, claims

    # --- trilha ---------------------------------------------------------------------
    async def _record(self, *args, **kwargs):
        """Grava negações/falhas; se a trilha cair, a resposta já é de recusa."""
        try:
            await self.trail.append(*args, **kwargs)
        except TrailUnavailable:
            pass

    async def trail_or_503(self, *args, **kwargs) -> tuple:
        try:
            return await self.trail.append(*args, **kwargs)
        except TrailUnavailable as err:
            raise Denied(503, "Trilha de auditoria indisponível: nenhum dado foi entregue.") from err

    async def require(self, user: dict | None, permission: str, action: str, ip: str, **target) -> None:
        if user is None:
            raise Denied(401, "Sessão ausente ou expirada.")
        if permission not in user["perms"]:
            await self._record(action, "DENIED", user["email"], user["roles"], ip=ip,
                               query={"missing_permission": permission, **target.pop("query", {})}, **target)
            raise Denied(403, f"Seu perfil não permite esta ação ({permission}).")

    def check_reason(self, permission: str, reason: str | None) -> str | None:
        policy = self.policy_store.get()
        reason = (reason or "").strip()
        if policy and permission in policy.reason_required and len(reason) < policy.reason_min_chars:
            raise Denied(400, f"Informe o motivo do acesso (mínimo de {policy.reason_min_chars} caracteres).")
        return reason[:500] or None

    def limit(self) -> int:
        policy = self.policy_store.get()
        return policy.max_results if policy else 50

    # --- operações ------------------------------------------------------------------
    async def search(self, user: dict | None, params: dict, ip: str) -> list:
        try:
            filters = clean_filters(params)
        except InvalidFilter as err:
            raise Denied(400, str(err)) from err
        await self.require(user, "SEARCH_CONVERSATIONS", "SEARCH_CONVERSATIONS", ip, query=dict(filters))
        if "text" in filters:
            await self.require(user, "SEARCH_CONTENT", "SEARCH_CONVERSATIONS", ip, query=dict(filters))
        try:
            rows = await self.registry.search(filters, self.limit())
        except RegistryUnavailable as err:
            raise Denied(503, "Registro de auditoria (Langfuse) indisponível.") from err
        await self.trail_or_503("SEARCH_CONVERSATIONS", "ALLOWED", user["email"], user["roles"], ip=ip,
                                targets=[filters["user"]] if "user" in filters else [],
                                conversation=filters.get("conversation"), query=filters, count=len(rows))
        return rows

    async def calls(self, user: dict | None, permission: str, action: str, ip: str, reason: str | None,
                    conversation: str | None = None, trace: str | None = None) -> tuple:
        target = {"conversation": conversation, "trace": trace}
        await self.require(user, permission, action, ip, **target)
        reason = self.check_reason(permission, reason)
        try:
            rows = await self.registry.calls(conversation=conversation, trace=trace, limit=self.limit())
        except RegistryUnavailable as err:
            raise Denied(503, "Registro de auditoria (Langfuse) indisponível.") from err
        if not rows:
            raise Denied(404, "Conversa não encontrada no registro.")
        # Grava ANTES de entregar: sem evento, o conteúdo lido é descartado.
        event = await self.trail_or_503(action, "ALLOWED", user["email"], user["roles"], ip=ip, reason=reason,
                                        targets=[r["user"] for r in rows if r["user"]], count=len(rows), **target)
        return rows, event, reason

    async def access_log(self, user: dict | None, ip: str) -> tuple:
        await self.require(user, "VIEW_ACCESS_LOG", "VIEW_ACCESS_LOG", ip)
        try:
            events, chain = await self.trail.recent(100), await self.trail.verify()
        except TrailUnavailable as err:
            raise Denied(503, "Trilha de auditoria indisponível.") from err
        await self.trail_or_503("VIEW_ACCESS_LOG", "ALLOWED", user["email"], user["roles"], ip=ip, count=len(events))
        return events, chain


def create_app(portal: Portal | None = None) -> FastAPI:
    if portal is None:
        portal = Portal(
            FileStore(os.environ.get("AUDIT_POLICY_PATH", "/app/policy/policy.yaml"), parse_policy, "política do portal"),
            FileStore(os.environ.get("MODEL_ACCESS_POLICY_PATH", "/app/policies/model-access.yaml"), parse_groups,
                      "política de grupos"),
            SessionSigner(os.environ["AUDIT_SESSION_SECRET"]),
            Trail(os.environ["AUDIT_DATABASE_URL"]),
            Registry(os.environ.get("LANGFUSE_URL", "http://langfuse-web:3000"), os.environ["LANGFUSE_PUBLIC_KEY"],
                     os.environ["LANGFUSE_SECRET_KEY"]),
            os.environ.get("LIBRECHAT_URL", "http://librechat:3080"),
        )
        portal.policy_store.get()
        portal.groups_store.get()

    app = FastAPI(title="Portal de auditoria", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.portal = portal
    templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    def ip_of(request: Request) -> str:
        return request.client.host if request.client else ""

    def page(request: Request, name: str, user: dict | None, status: int = 200, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, {"user": user, **ctx}, status_code=status)

    def error_page(request: Request, user, err: Denied) -> Response:
        if err.status == 401:
            return RedirectResponse("/login", status_code=303)
        return page(request, "error.html", user, status=err.status, message=err.message)

    def api_error(err: Denied) -> JSONResponse:
        return JSONResponse({"error": err.message}, status_code=err.status)

    async def form(request: Request) -> dict:
        return {k: v for k, v in (await request.form()).items()}

    def csrf_ok(user, data) -> bool:
        return user is not None and portal.signer.csrf_ok(user["claims"], data.get("csrf"))

    def set_cookie(response: Response, token: str, claims: dict) -> Response:
        response.set_cookie(COOKIE, token, max_age=max(0, claims["exp"] - int(time.time())), httponly=True,
                            samesite="strict", path="/")
        return response

    # --- saúde ------------------------------------------------------------------------
    @app.get("/health")
    async def health():
        ok = await portal.trail.ping()
        return JSONResponse({"status": "ok" if ok else "trail_unavailable"}, status_code=200 if ok else 503)

    # --- login --------------------------------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request):
        return page(request, "login.html", None)

    @app.post("/login")
    async def login_html(request: Request):
        data = await form(request)
        try:
            token, claims = await portal.login(data.get("email", ""), data.get("password", ""), ip_of(request))
        except Denied as err:
            return page(request, "login.html", None, status=err.status, message=err.message, email=data.get("email", ""))
        return set_cookie(RedirectResponse("/", status_code=303), token, claims)

    @app.post("/api/login")
    async def login_api(request: Request):
        data = await request.json()
        try:
            token, claims = await portal.login(data.get("email", ""), data.get("password", ""), ip_of(request))
        except Denied as err:
            return api_error(err)
        roles, perms = resolve(portal.policy_store.get(), portal.groups_store.get(), claims["sub"])
        return set_cookie(JSONResponse({"email": claims["sub"], "roles": list(roles), "permissions": sorted(perms),
                                        "expires_at": claims["exp"]}), token, claims)

    async def logout(request: Request, response: Response) -> Response:
        user = portal.who(request)
        if user:
            portal.signer.revoke(user["claims"])
            await portal._record("LOGOUT", "ALLOWED", user["email"], user["roles"], ip=ip_of(request))
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.post("/logout")
    async def logout_html(request: Request):
        return await logout(request, RedirectResponse("/login", status_code=303))

    @app.post("/api/logout")
    async def logout_api(request: Request):
        return await logout(request, JSONResponse({"status": "ok"}))

    # --- busca ----------------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def search_html(request: Request):
        user = portal.who(request)
        params = dict(request.query_params)
        rows, message = None, None
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if params.get("run"):
            try:
                rows = await portal.search(user, params, ip_of(request))
            except Denied as err:
                if err.status in (401,):
                    return error_page(request, user, err)
                message = err.message
        return page(request, "search.html", user, rows=rows, params=params, message=message)

    @app.get("/api/search")
    async def search_api(request: Request):
        try:
            rows = await portal.search(portal.who(request), dict(request.query_params), ip_of(request))
        except Denied as err:
            return api_error(err)
        return {"count": len(rows), "results": rows}

    # --- trilha -------------------------------------------------------------------------------
    @app.get("/access-log", response_class=HTMLResponse)
    async def access_log_html(request: Request):
        user = portal.who(request)
        try:
            events, chain = await portal.access_log(user, ip_of(request))
        except Denied as err:
            return error_page(request, user, err)
        return page(request, "access_log.html", user, events=events, chain=chain)

    @app.get("/api/access-log")
    async def access_log_api(request: Request):
        try:
            events, chain = await portal.access_log(portal.who(request), ip_of(request))
        except Denied as err:
            return api_error(err)
        return JSONResponse(json.loads(json.dumps({"chain": chain, "events": events}, default=str)))

    # --- conversa: visualizar e exportar ----------------------------------------------------
    # Rotas genéricas /{kind}/{ident} por último: /access-log e /api/... acima têm precedência.
    def target_of(kind: str, ident: str) -> dict:
        if kind not in ("conversations", "traces") or not ident or len(ident) > 200:
            raise Denied(404, "Não encontrado.")
        return {"conversation": ident} if kind == "conversations" else {"trace": ident}

    @app.get("/{kind}/{ident}", response_class=HTMLResponse)
    async def reason_form(request: Request, kind: str, ident: str):
        user = portal.who(request)
        try:
            target = target_of(kind, ident)
            if user is None:
                raise Denied(401, "")
            # Sem permissão: recusa (e registra) já ao abrir, sem pedir motivo.
            await portal.require(user, "VIEW_CONVERSATION", "VIEW_CONVERSATION", ip_of(request), **target)
        except Denied as err:
            return error_page(request, user, err)
        return page(request, "reason.html", user, kind=kind, ident=ident, target=target)

    @app.post("/{kind}/{ident}", response_class=HTMLResponse)
    async def view_html(request: Request, kind: str, ident: str):
        user = portal.who(request)
        data = await form(request)
        try:
            target = target_of(kind, ident)
            if user is None:
                raise Denied(401, "")
            if not csrf_ok(user, data):
                raise Denied(403, "Formulário expirado; recarregue a página.")
            rows, event, reason = await portal.calls(user, "VIEW_CONVERSATION", "VIEW_CONVERSATION", ip_of(request),
                                                     data.get("reason"), **target)
        except Denied as err:
            if err.status == 400:
                return page(request, "reason.html", user, status=400, kind=kind, ident=ident,
                            target=target_of(kind, ident), message=err.message)
            return error_page(request, user, err)
        return page(request, "conversation.html", user, rows=rows, event=event, reason=reason, kind=kind,
                    ident=ident, target=target)

    def export_payload(user, rows, event, reason, target) -> dict:
        return {"exported_at": datetime.now(timezone.utc).isoformat(), "exported_by": user["email"],
                "reason": reason, "access_event_id": event[0], "access_event_hash": event[1],
                **{f"{k}_id": v for k, v in target.items()}, "calls": rows}

    def attachment(payload: dict, ident: str) -> Response:
        safe = "".join(c for c in ident if c.isalnum() or c in "-_")[:80]
        return Response(json.dumps(payload, ensure_ascii=False, indent=2, default=str), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="conversa-{safe}.json"'})

    @app.post("/{kind}/{ident}/export")
    async def export_html(request: Request, kind: str, ident: str):
        user = portal.who(request)
        data = await form(request)
        try:
            target = target_of(kind, ident)
            if user is None:
                raise Denied(401, "")
            if not csrf_ok(user, data):
                raise Denied(403, "Formulário expirado; recarregue a página.")
            rows, event, reason = await portal.calls(user, "EXPORT_CONVERSATION", "EXPORT_CONVERSATION",
                                                     ip_of(request), data.get("reason"), **target)
        except Denied as err:
            return error_page(request, user, err)
        return attachment(export_payload(user, rows, event, reason, target), ident)

    @app.post("/api/{kind}/{ident}")
    async def view_api(request: Request, kind: str, ident: str):
        try:
            target = target_of(kind, ident)
            data = await request.json()
            rows, event, reason = await portal.calls(portal.who(request), "VIEW_CONVERSATION", "VIEW_CONVERSATION",
                                                     ip_of(request), data.get("reason"), **target)
        except Denied as err:
            return api_error(err)
        return {"access_event_id": event[0], "reason": reason, "count": len(rows), "calls": rows}

    @app.post("/api/{kind}/{ident}/export")
    async def export_api(request: Request, kind: str, ident: str):
        try:
            target = target_of(kind, ident)
            data = await request.json()
            user = portal.who(request)
            rows, event, reason = await portal.calls(user, "EXPORT_CONVERSATION", "EXPORT_CONVERSATION",
                                                     ip_of(request), data.get("reason"), **target)
        except Denied as err:
            return api_error(err)
        return attachment(export_payload(user, rows, event, reason, target), ident)

    return app


app = create_app() if os.environ.get("AUDIT_SESSION_SECRET") else None
