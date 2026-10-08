"""Sessão do portal: token assinado (HMAC-SHA256) em cookie, revogação em memória e CSRF.

O token guarda só e-mail, ID da sessão e expiração. As permissões são recalculadas a cada
requisição, então tirar o usuário de um grupo vale imediatamente.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SessionSigner:
    def __init__(self, secret: str):
        if len(secret) < 32:
            raise ValueError("AUDIT_SESSION_SECRET deve ter ao menos 32 caracteres")
        self._key = secret.encode()
        self._revoked = {}

    def _sign(self, payload: bytes) -> str:
        return _b64(hmac.new(self._key, payload, hashlib.sha256).digest())

    def issue(self, email: str, minutes: int, now: float | None = None) -> tuple:
        now = now or time.time()
        claims = {"sub": email, "sid": secrets.token_urlsafe(16), "exp": int(now + minutes * 60)}
        payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
        return f"{_b64(payload)}.{self._sign(payload)}", claims

    def verify(self, token: str | None, now: float | None = None) -> dict | None:
        if not token or token.count(".") != 1:
            return None
        body, signature = token.split(".")
        try:
            payload = _unb64(body)
            if not hmac.compare_digest(signature, self._sign(payload)):
                return None
            claims = json.loads(payload)
        except Exception:
            return None
        now = now or time.time()
        if not isinstance(claims, dict) or claims.get("exp", 0) <= now or claims.get("sid") in self._revoked:
            return None
        return claims

    def revoke(self, claims: dict) -> None:
        self._revoked[claims["sid"]] = claims["exp"]
        now = time.time()
        for sid in [s for s, exp in self._revoked.items() if exp <= now]:
            self._revoked.pop(sid, None)

    def csrf(self, claims: dict) -> str:
        return hmac.new(self._key, f"csrf:{claims['sid']}".encode(), hashlib.sha256).hexdigest()[:40]

    def csrf_ok(self, claims: dict, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(token, self.csrf(claims))
