"""Permissões do portal de auditoria e grupos dos usuários.

- policy.yaml (deste portal): grupo → permissões, motivo obrigatório, sessão, limite.
- model-access.yaml (do gateway): e-mail → grupos. Fonte única de grupos da plataforma.

Os dois arquivos são recarregados quando mudam (data e tamanho). Arquivo ausente ou
inválido → None: ninguém recebe permissão (fail-closed).
"""

import logging
import os
from dataclasses import dataclass

import yaml

PERMISSIONS = frozenset({"SEARCH_CONVERSATIONS", "SEARCH_CONTENT", "VIEW_CONVERSATION",
                         "EXPORT_CONVERSATION", "VIEW_ACCESS_LOG"})

logger = logging.getLogger("audit_portal.policy")


class PolicyError(ValueError):
    """Política ausente ou inválida."""


@dataclass(frozen=True)
class AuditPolicy:
    roles: dict
    reason_required: frozenset
    reason_min_chars: int
    session_minutes: int
    max_results: int


@dataclass(frozen=True)
class Groups:
    users: dict
    default_group: str | None

    def of(self, email: str) -> tuple:
        return tuple(self.users.get(email.strip().lower(), [self.default_group] if self.default_group else []))


def _positive_int(raw: dict, key: str, default: int) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PolicyError(f"'{key}' deve ser inteiro positivo")
    return value


def parse_policy(raw) -> AuditPolicy:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise PolicyError("política deve ser um mapeamento com version: 1")
    roles_raw = raw.get("roles")
    if not isinstance(roles_raw, dict) or not roles_raw:
        raise PolicyError("'roles' deve mapear grupos para permissões")
    roles = {}
    for group, perms in roles_raw.items():
        if not isinstance(perms, list) or not perms:
            raise PolicyError(f"grupo {group!r}: lista de permissões vazia ou inválida")
        unknown = set(perms) - PERMISSIONS
        if unknown:
            raise PolicyError(f"grupo {group!r}: permissões desconhecidas {sorted(unknown)}")
        roles[str(group)] = frozenset(perms)
    required = raw.get("reason_required", [])
    if not isinstance(required, list) or set(required) - PERMISSIONS:
        raise PolicyError("'reason_required' deve listar permissões conhecidas")
    return AuditPolicy(roles, frozenset(required), _positive_int(raw, "reason_min_chars", 10),
                       _positive_int(raw, "session_minutes", 30), _positive_int(raw, "max_results", 200))


def parse_groups(raw) -> Groups:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise PolicyError("política de acesso deve ter version: 1")
    users_raw = raw.get("users") or {}
    if not isinstance(users_raw, dict):
        raise PolicyError("'users' deve ser um mapeamento")
    users = {}
    for email, groups in users_raw.items():
        if not isinstance(groups, list):
            raise PolicyError(f"usuário {email!r}: grupos devem ser uma lista")
        users[str(email).strip().lower()] = [str(g) for g in groups]
    default = raw.get("default_group")
    return Groups(users, str(default) if default else None)


class FileStore:
    """Lê e valida um YAML; recarrega quando o arquivo muda; None se ausente ou inválido."""

    def __init__(self, path: str, parser, label: str):
        self.path, self.parser, self.label = path, parser, label
        self._stamp, self._value = None, None

    def get(self):
        try:
            st = os.stat(self.path)
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError as err:
            if self._stamp != "missing":
                logger.error("audit_portal: %s indisponível (%s) — acesso negado a todos", self.label, err)
            self._stamp, self._value = "missing", None
            return None
        if stamp != self._stamp:
            try:
                with open(self.path, encoding="utf-8") as fh:
                    self._value = self.parser(yaml.safe_load(fh))
                logger.warning("audit_portal: %s carregada de %s", self.label, self.path)
            except Exception as err:  # YAML quebrado ou estrutura inválida
                logger.error("audit_portal: %s inválida (%s) — acesso negado a todos", self.label, err)
                self._value = None
            self._stamp = stamp
        return self._value


def resolve(policy: AuditPolicy | None, groups: Groups | None, email: str) -> tuple:
    """(perfis de auditoria do usuário, permissões). Sem política válida → nada."""
    if policy is None or groups is None or not email:
        return (), frozenset()
    roles = tuple(sorted(g for g in set(groups.of(email)) if g in policy.roles))
    perms = frozenset().union(*(policy.roles[r] for r in roles)) if roles else frozenset()
    return roles, perms
