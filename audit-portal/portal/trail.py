"""Trilha de acesso dos auditores (PostgreSQL, banco audit).

Grava só pela função audit.append_access_event (hash encadeado; a tabela não aceita
UPDATE/DELETE). Qualquer falha vira TrailUnavailable: quem chama não entrega o resultado.
Cada evento também vai para o log do container (cópia fora do banco).
"""

import json
import logging

import psycopg

logger = logging.getLogger("audit_portal.trail")


class TrailUnavailable(RuntimeError):
    """Não foi possível gravar ou ler a trilha."""


class Trail:
    def __init__(self, dsn: str, timeout: int = 5):
        self.dsn, self.timeout = dsn, timeout

    async def _connect(self):
        return await psycopg.AsyncConnection.connect(self.dsn, connect_timeout=self.timeout, autocommit=True)

    async def append(self, action: str, outcome: str, actor: str, roles=(), channel: str = "portal",
                     targets=(), conversation: str | None = None, trace: str | None = None,
                     query: dict | None = None, reason: str | None = None, count: int | None = None,
                     ip: str | None = None) -> tuple:
        try:
            async with await self._connect() as conn:
                cur = await conn.execute(
                    "SELECT event_id, event_hash FROM audit.append_access_event("
                    "%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)",
                    (action, outcome, actor, list(roles), channel, sorted(set(targets)), conversation, trace,
                     json.dumps(query or {}, ensure_ascii=False, sort_keys=True), reason, count, ip))
                event_id, event_hash = await cur.fetchone()
        except Exception as err:
            logger.error("AUDIT_ACCESS não gravado action=%s actor=%s (%s)", action, actor, type(err).__name__)
            raise TrailUnavailable(str(err)) from err
        logger.warning("AUDIT_ACCESS id=%s action=%s outcome=%s actor=%s conversation=%s trace=%s hash=%s",
                       event_id, action, outcome, actor, conversation or "-", trace or "-", event_hash)
        return event_id, event_hash

    async def recent(self, limit: int = 100) -> list:
        try:
            async with await self._connect() as conn:
                cur = await conn.execute(
                    "SELECT id, occurred_at, action, outcome, actor, actor_roles, channel, target_users, "
                    "conversation_id, trace_id, query, reason, result_count, client_ip, hash "
                    "FROM audit.access_log ORDER BY id DESC LIMIT %s", (limit,))
                cols = [c.name for c in cur.description]
                return [dict(zip(cols, row)) for row in await cur.fetchall()]
        except Exception as err:
            raise TrailUnavailable(str(err)) from err

    async def verify(self) -> dict:
        try:
            async with await self._connect() as conn:
                cur = await conn.execute("SELECT total, first_invalid_id, head_hash FROM audit.verify_access_chain()")
                total, invalid, head = await cur.fetchone()
                return {"total": total, "first_invalid_id": invalid, "head_hash": head, "intact": invalid is None}
        except Exception as err:
            raise TrailUnavailable(str(err)) from err

    async def ping(self) -> bool:
        try:
            async with await self._connect() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False
