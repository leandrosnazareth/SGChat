"""Controle de acesso a modelos por usuário/grupo (AI Gateway).

Registrado em litellm/config.yaml como `custom.model_access.handler`. O usuário
é o identificador de usuário final do LiteLLM (`end_user_id`, preenchido pelo
portal com o e-mail via `x-litellm-end-user-id`); seus grupos vêm da política YAML.
- Chamadas a modelo não permitido: HTTP 403 `MODEL_ACCESS_DENIED`.
- Listagens (`/v1/models` etc.): só os modelos permitidos.
Fail-closed: política ausente/inválida ou falta de identidade negam o acesso
(listagem vazia). Spec: openspec/specs/model-access.

O módulo é autocontido: o LiteLLM o carrega pelo caminho do arquivo, sem
registrá-lo em sys.modules (por isso não usa `from __future__ import annotations`,
que quebraria os dataclasses).
"""

import logging
import os
import threading
from dataclasses import dataclass, field

import yaml
from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger

POLICY_PATH = os.environ.get("MODEL_ACCESS_POLICY_PATH", "/app/policies/model-access.yaml")
ADMIN_ROLE = "proxy_admin"
DENIED = "MODEL_ACCESS_DENIED"

logger = logging.getLogger("corporate.model_access")


class PolicyError(ValueError):
    """Política ausente ou estruturalmente inválida."""


@dataclass(frozen=True)
class Policy:
    default_group: str
    groups: dict[str, frozenset[str]]
    users: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def groups_for(self, email: str) -> tuple[str, ...]:
        return self.users.get(email.strip().lower(), (self.default_group,))

    def allowed_models(self, email: str) -> frozenset[str]:
        allowed: set[str] = set()
        for group in self.groups_for(email):
            allowed |= self.groups[group]
        return frozenset(allowed)


def parse_policy(raw: object) -> Policy:
    """Valida a estrutura da política; qualquer violação gera PolicyError."""
    if not isinstance(raw, dict):
        raise PolicyError("a política deve ser um mapeamento YAML")
    if raw.get("version") != 1:
        raise PolicyError(f"versão de política não suportada: {raw.get('version')!r}")

    groups_raw = raw.get("groups")
    if not isinstance(groups_raw, dict) or not groups_raw:
        raise PolicyError("'groups' deve ser um mapeamento não vazio")
    groups: dict[str, frozenset[str]] = {}
    for name, spec in groups_raw.items():
        models = spec.get("models") if isinstance(spec, dict) else None
        if not isinstance(models, list) or not models or not all(isinstance(m, str) and m for m in models):
            raise PolicyError(f"grupo {name!r}: 'models' deve ser uma lista não vazia de nomes")
        groups[str(name)] = frozenset(models)

    default_group = raw.get("default_group")
    if default_group not in groups:
        raise PolicyError(f"'default_group' {default_group!r} não existe em 'groups'")

    users_raw = raw.get("users") or {}
    if not isinstance(users_raw, dict):
        raise PolicyError("'users' deve ser um mapeamento e-mail → lista de grupos")
    users: dict[str, tuple[str, ...]] = {}
    for email, user_groups in users_raw.items():
        if not isinstance(user_groups, list) or not user_groups:
            raise PolicyError(f"usuário {email!r}: informe uma lista não vazia de grupos")
        unknown = [g for g in user_groups if g not in groups]
        if unknown:
            raise PolicyError(f"usuário {email!r}: grupo(s) inexistente(s) {unknown}")
        users[str(email).strip().lower()] = tuple(user_groups)

    return Policy(default_group=default_group, groups=groups, users=users)


class PolicyStore:
    """Carrega a política e recarrega quando o arquivo muda (mtime/tamanho)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._signature: tuple[float, int] | None = None
        self._policy: Policy | None = None
        self._error: str | None = "política ainda não carregada"

    def get(self) -> Policy | None:
        """Retorna a política vigente ou None se ausente/inválida (fail-closed)."""
        try:
            st = os.stat(self.path)
            signature = (st.st_mtime, st.st_size)
        except OSError as err:
            self._set_error(f"arquivo de política inacessível: {err}")
            return None
        if signature != self._signature:
            with self._lock:
                if signature != self._signature:
                    self._reload(signature)
        return self._policy

    def _reload(self, signature: tuple[float, int]) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                policy = parse_policy(yaml.safe_load(fh))
        except (OSError, yaml.YAMLError, PolicyError) as err:
            self._signature = signature
            self._set_error(f"política inválida em {self.path}: {err}")
            return
        self._signature, self._policy, self._error = signature, policy, None
        logger.warning(
            "model_access: política carregada de %s (%d grupos, %d usuários, grupo padrão %s)",
            self.path, len(policy.groups), len(policy.users), policy.default_group,
        )

    def _set_error(self, message: str) -> None:
        if message != self._error:
            logger.error("model_access: %s — negando todas as requisições de usuários", message)
        self._policy, self._error = None, message


def _identity(user_api_key_dict) -> str | None:
    """E-mail do usuário final, como identificado pelo LiteLLM na autenticação."""
    end_user = getattr(user_api_key_dict, "end_user_id", None)
    if isinstance(end_user, str) and end_user.strip():
        return end_user.strip().lower()
    return None


def _is_admin(user_api_key_dict) -> bool:
    role = getattr(user_api_key_dict, "user_role", None)
    return getattr(role, "value", role) == ADMIN_ROLE


def _deny(user: str | None, model: str | None, reason: str, groups: tuple[str, ...] = ()) -> HTTPException:
    logger.warning(
        "model_access: NEGADO user=%s model=%s reason=%s groups=%s",
        user or "-", model or "-", reason, ",".join(groups) or "-",
    )
    return HTTPException(status_code=403, detail={"error": DENIED, "reason": reason, "model": model})


class ModelAccessControl(CustomLogger):
    def __init__(self, store: PolicyStore):
        super().__init__()
        self.store = store
        self.store.get()  # carrega (e loga) já na inicialização

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        model = data.get("model")
        if model is None:
            return data
        if _is_admin(user_api_key_dict):
            return data  # chave mestra: acesso administrativo (spec "Acesso administrativo")

        user = _identity(user_api_key_dict)
        reason, groups = None, ()
        if user is None:
            reason = "missing_identity"
        else:
            policy = self.store.get()
            if policy is None:
                reason = "policy_unavailable"
            elif model not in policy.allowed_models(user):
                reason, groups = "model_not_allowed", policy.groups_for(user)
        if reason is None:
            return data
        # Decisão no registro de auditoria (Langfuse) da requisição negada.
        data.setdefault("metadata", {}).setdefault("spend_logs_metadata", {}).update(
            {"requested_model": model, "routing_reason": DENIED, "access_reason": reason, "blocked": True})
        raise _deny(user, model, reason, groups)

    async def async_filter_listed_models(self, user_api_key_dict, model_names):
        if _is_admin(user_api_key_dict):
            return list(model_names)
        user = _identity(user_api_key_dict)
        policy = self.store.get() if user else None
        if policy is None:
            return []  # sem identidade ou sem política válida: nada visível (fail-closed)
        allowed = policy.allowed_models(user)
        return [name for name in model_names if name in allowed]


handler = ModelAccessControl(PolicyStore(POLICY_PATH))
