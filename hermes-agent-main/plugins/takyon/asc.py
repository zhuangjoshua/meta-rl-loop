"""App Store Connect — the minimal ASC leaf (App Store rail, readmodular.md §4.1/§6.3).

Pure leaf, custody-agnostic BY DESIGN: every function takes the ASC key material
(``key_id`` / ``issuer_id`` / ``private_key_pem``) as EXPLICIT arguments. It never reads
``os.environ``, never touches the safebox, and never caches key material — the caller (today: an
operator-rail probe with the key resolved upstream; later: the safebox broker route, which
resolves the key locally and passes it in per GOAL_RULES §7) owns custody. That keeps this leaf
usable unchanged when the .p8 moves from the operator's machine into safebox/Doppler custody.

Scope (deliberately tiny — the general-apps manifest work will grow routes on the broker, not
here): mint the short-TTL ES256 JWT ASC auth requires, and the ONE probe the plan makes
load-bearing — ``probe_account_health`` — which answers "is the Apple account able to ship?"
Apple has NO agreements-status endpoint; the only machine signal is
``403 FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED`` on ordinary endpoints, which fires at
ENFORCEMENT time (later than the UI banner). The classification below encodes exactly that
honesty: ``agreement_blocked`` is definitive; ``ok`` means "not currently enforced", not "no
banner pending".

House style: stdlib + lazy third-party imports (PyJWT/httpx are in the runtime venv), typed
errors, receipt-shaped dict results, no side effects.
"""

from __future__ import annotations

import time
from typing import Any

ASC_BASE_URL = "https://api.appstoreconnect.apple.com"
ASC_AUDIENCE = "appstoreconnect-v1"
# Apple rejects ASC JWTs with exp more than 20 minutes out; stay comfortably under.
MAX_JWT_TTL_SECONDS = 20 * 60
DEFAULT_JWT_TTL_SECONDS = 10 * 60

# Health states, ordered from good to bad. Receipts carry the state string, never a bool —
# the pulse block and gate errors name the state directly.
HEALTH_OK = "ok"                            # authenticated request succeeded
HEALTH_AGREEMENT_BLOCKED = "agreement_blocked"  # 403 REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED
HEALTH_AUTH_ERROR = "auth_error"            # 401 — key revoked/clock skew/bad kid-issuer pair
HEALTH_ERROR = "error"                      # other HTTP error (4xx/5xx)
HEALTH_UNREACHABLE = "unreachable"          # network failure — says nothing about the account

AGREEMENT_ERROR_CODE = "FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED"


class AscError(Exception):
    """Base for ASC leaf errors (bad inputs; transport errors are classified, not raised)."""


def mint_asc_jwt(
    key_id: str,
    issuer_id: str,
    private_key_pem: str,
    *,
    ttl_seconds: int = DEFAULT_JWT_TTL_SECONDS,
) -> str:
    """Mint the short-TTL ES256 JWT the ASC API requires. The TTL is clamped to Apple's 20-minute
    ceiling — a longer request is a caller bug and raises rather than silently minting a token
    Apple will refuse."""
    kid = str(key_id or "").strip()
    iss = str(issuer_id or "").strip()
    pem = str(private_key_pem or "").strip()
    if not kid or not iss or not pem:
        raise AscError("mint_asc_jwt requires key_id, issuer_id, and private_key_pem")
    ttl = int(ttl_seconds)
    if ttl <= 0 or ttl > MAX_JWT_TTL_SECONDS:
        raise AscError(
            f"ASC JWT ttl_seconds must be in (0, {MAX_JWT_TTL_SECONDS}]; Apple refuses exp more "
            f"than 20 minutes out (got {ttl_seconds})"
        )
    import jwt  # lazy: PyJWT

    now = int(time.time())
    return jwt.encode(
        {"iss": iss, "iat": now, "exp": now + ttl, "aud": ASC_AUDIENCE},
        pem,
        algorithm="ES256",
        headers={"kid": kid, "typ": "JWT"},
    )


def _classify_response(status_code: int, body: Any) -> str:
    """Map an ASC HTTP response to a health state. The agreement block is detected by the exact
    error code Apple returns once enforcement starts — matching on the code, not prose."""
    if 200 <= status_code < 300:
        return HEALTH_OK
    if status_code == 401:
        return HEALTH_AUTH_ERROR
    if status_code == 403:
        errors = []
        if isinstance(body, dict):
            raw = body.get("errors")
            if isinstance(raw, list):
                errors = [e for e in raw if isinstance(e, dict)]
        for err in errors:
            if str(err.get("code") or "").strip() == AGREEMENT_ERROR_CODE:
                return HEALTH_AGREEMENT_BLOCKED
        return HEALTH_ERROR
    return HEALTH_ERROR


def probe_account_health(
    key_id: str,
    issuer_id: str,
    private_key_pem: str,
    *,
    timeout_seconds: float = 15.0,
    base_url: str = ASC_BASE_URL,
    transport: Any = None,
) -> dict[str, Any]:
    """The account-health probe (readmodular §4.1): one cheap authenticated GET
    (``/v1/bundleIds?limit=1``), classified. Never raises on transport/HTTP failure — it returns a
    receipt-shaped dict so pulse/pre-wake callers stay best-effort ("never break the wake"):

        {"state": ok|agreement_blocked|auth_error|error|unreachable,
         "status_code": int|None, "detail": str, "checked_at": epoch_seconds}

    HONEST LIMIT carried in the receipt semantics: ``agreement_blocked`` fires only once Apple
    ENFORCES a pending agreement — a banner pending in the UI reads as ``ok`` here. ``transport``
    is a test seam (httpx transport)."""
    checked_at = int(time.time())

    def _receipt(state: str, *, status_code: int | None = None, detail: str = "") -> dict[str, Any]:
        return {
            "state": state,
            "status_code": status_code,
            "detail": detail[:300],
            "checked_at": checked_at,
        }

    try:
        token = mint_asc_jwt(key_id, issuer_id, private_key_pem)
    except AscError as exc:
        return _receipt(HEALTH_ERROR, detail=f"jwt_mint_failed: {exc}")
    except Exception as exc:  # bad PEM etc. — classified, never raised into a wake
        return _receipt(HEALTH_ERROR, detail=f"jwt_mint_failed: {exc}")

    import httpx  # lazy

    try:
        with httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
        ) as client:
            resp = client.get(
                "/v1/bundleIds",
                params={"limit": 1},
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as exc:
        return _receipt(HEALTH_UNREACHABLE, detail=str(exc))

    try:
        body = resp.json()
    except Exception:
        body = None
    state = _classify_response(resp.status_code, body)
    detail = ""
    if state != HEALTH_OK and isinstance(body, dict):
        raw = body.get("errors")
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            first = raw[0]
            detail = f"{first.get('code') or ''}: {first.get('detail') or first.get('title') or ''}"
    return _receipt(state, status_code=resp.status_code, detail=detail)
