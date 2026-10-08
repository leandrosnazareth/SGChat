"""Bootstrap do LiteLLM (serviço one-shot litellm-bootstrap). Idempotente.

1. Garante que a chave virtual do portal (LITELLM_PORTAL_KEY) exista
   (POST /key/generate com a master key); se já existe, não a altera.
2. Garante o budget nativo de rate limit por usuário final
   (corporate-end-user-default, RATE_LIMIT_RPM/RATE_LIMIT_TPM); cria ou atualiza os limites.
3. Associa esse budget à chave do portal (metadata.end_user_budget_id): no
   LiteLLM v1.104, requisições com chave virtual só aplicam o budget padrão de
   usuário final se a própria chave o declarar (litellm_settings.max_end_user_budget_id
   cobre os demais caminhos de autenticação).
"""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("LITELLM_URL", "http://litellm:4000")
MASTER_KEY = os.environ["LITELLM_MASTER_KEY"]
PORTAL_KEY = os.environ["LITELLM_PORTAL_KEY"]
KEY_ALIAS = "librechat-portal"
END_USER_BUDGET_ID = "corporate-end-user-default"
RATE_LIMIT_RPM = int(os.environ.get("RATE_LIMIT_RPM", "30"))
RATE_LIMIT_TPM = int(os.environ.get("RATE_LIMIT_TPM", "50000"))


def request(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list]:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {MASTER_KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def wait_for_gateway() -> None:
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health/readiness", timeout=5) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(2)
    sys.exit("bootstrap: LiteLLM não ficou pronto")


def ensure_end_user_budget() -> None:
    limits = {"budget_id": END_USER_BUDGET_ID, "rpm_limit": RATE_LIMIT_RPM, "tpm_limit": RATE_LIMIT_TPM}
    status, body = request("POST", "/budget/info", {"budgets": [END_USER_BUDGET_ID]})
    if status != 200:
        sys.exit(f"bootstrap: falha ao consultar budget (HTTP {status}): {body}")
    existing = body[0] if isinstance(body, list) and body else None
    if existing is None:
        status, body = request("POST", "/budget/new", limits)
        verb = "criado"
    elif existing.get("rpm_limit") != RATE_LIMIT_RPM or existing.get("tpm_limit") != RATE_LIMIT_TPM:
        status, body = request("POST", "/budget/update", limits)
        verb = "atualizado"
    else:
        print(f"bootstrap: budget '{END_USER_BUDGET_ID}' já com rpm={RATE_LIMIT_RPM} tpm={RATE_LIMIT_TPM}; nada a fazer.")
        return
    if status != 200:
        sys.exit(f"bootstrap: falha ao gravar budget (HTTP {status}): {body}")
    print(f"bootstrap: budget '{END_USER_BUDGET_ID}' {verb} (rpm={RATE_LIMIT_RPM} tpm={RATE_LIMIT_TPM}).")


def main() -> None:
    if not PORTAL_KEY.startswith("sk-") or len(PORTAL_KEY) < 16:
        sys.exit("bootstrap: LITELLM_PORTAL_KEY deve começar com 'sk-' e ter ao menos 16 caracteres")

    wait_for_gateway()
    ensure_end_user_budget()

    # Consulta pelo hash SHA-256 (formato usado pelo LiteLLM) para a chave em
    # texto puro não aparecer em logs de acesso.
    key_hash = hashlib.sha256(PORTAL_KEY.encode()).hexdigest()
    status, info = request("GET", f"/key/info?key={key_hash}")
    if status != 200:
        status, body = request("POST", "/key/generate", {
            "key": PORTAL_KEY, "key_alias": KEY_ALIAS,
            "metadata": {"end_user_budget_id": END_USER_BUDGET_ID},
        })
        if status != 200:
            sys.exit(f"bootstrap: falha ao criar a chave (HTTP {status}): {body}")
        print(f"bootstrap: chave '{KEY_ALIAS}' criada (budget de usuário final {END_USER_BUDGET_ID}).")
        return

    metadata = dict((info.get("info") or {}).get("metadata") or {})
    if metadata.get("end_user_budget_id") == END_USER_BUDGET_ID:
        print(f"bootstrap: chave '{KEY_ALIAS}' já existe e usa o budget {END_USER_BUDGET_ID}; nada a fazer.")
        return
    metadata["end_user_budget_id"] = END_USER_BUDGET_ID
    status, body = request("POST", "/key/update", {"key": PORTAL_KEY, "metadata": metadata})
    if status != 200:
        sys.exit(f"bootstrap: falha ao associar o budget à chave (HTTP {status}): {body}")
    print(f"bootstrap: chave '{KEY_ALIAS}' associada ao budget de usuário final {END_USER_BUDGET_ID}.")


if __name__ == "__main__":
    main()
