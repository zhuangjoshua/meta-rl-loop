"""Safebox authority for sensitive env-backed values and top-level auth state.

The backing store for deployed Takyon secrets may still be process env
(e.g. Vercel/runtime injection) or ``TAKYON_HOME/.env``. The authority
boundary lives here: callers should route secret and funding-sensitive
reads and writes through Safebox instead of touching ``os.environ`` or
parsing/writing the env file directly.

This module exposes only typed env authority for keys that look secret-
backed or are explicitly sensitive (for example database URLs). It does
not provide a generic config store for arbitrary dashboard settings.

Safebox also owns the top-level ``tk_...`` API-key verifier registry. The
control plane may mirror key metadata into Postgres for joins/audit, but the
granting authority for a presented raw key lives here, not in a mutable DB row.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from takyon_constants import get_takyon_home
from takyon_cli.config import (
    _normalize_env_value_for_storage,
    _remove_env_value_direct,
    _save_env_value_direct,
    _validate_env_key,
    is_managed,
    load_env,
    managed_error,
)

from .user_api_keys import hash_api_key, is_well_formed, key_prefix
from .business_credits import (
    CreativeCreditBalances,
    CreativeCreditReservation,
    InsufficientCreativeCredits,
    UnknownCreativeCreditReservation,
)

_EXACT_SENSITIVE_ENV_KEYS = frozenset(
    {
        "AUTH0_CLIENT_SECRET",
        "AUTH0_SECRET",
        "DATABASE_URL",
        "POSTGRES_PRISMA_URL",
        "POSTGRES_URL",
    }
)

_SENSITIVE_ENV_SUFFIXES = (
    "_ACCESS_KEY_ID",
    "_ACCESS_TOKEN",
    "_API_KEY",
    "_CLIENT_SECRET",
    "_KEY",
    "_PASSWORD",
    "_PRIVATE_KEY",
    "_SECRET",
    "_SECRET_ACCESS_KEY",
    "_TOKEN",
    "_WEBHOOK_SECRET",
)

_SAFEBOX_DIRNAME = "safebox"
_USER_API_KEYS_FILE_NAME = "user_api_keys.json"
_USER_API_KEYS_STATE_VERSION = 1
_USER_API_KEYS_MUTEX = threading.RLock()
_SAFEBOX_REMOTE_URL_ENV = "TAKYON_SAFEBOX_URL"
_SAFEBOX_REMOTE_TOKEN_ENV = "TAKYON_SAFEBOX_TOKEN"


class RemoteSafeboxError(RuntimeError):
    """A Safebox remote request failed with a concrete HTTP status/payload."""

    def __init__(self, message: str, *, status_code: int, payload: dict[str, Any]):
        super().__init__(message)
        self.status_code = int(status_code)
        self.payload = payload


def is_sensitive_env_key(key: str) -> bool:
    name = str(key or "").strip()
    if not name:
        return False
    if name in _EXACT_SENSITIVE_ENV_KEYS:
        return True
    return name.endswith(_SENSITIVE_ENV_SUFFIXES)


def _remote_base_url() -> str:
    return str(os.environ.get(_SAFEBOX_REMOTE_URL_ENV) or "").strip().rstrip("/")


def _remote_enabled() -> bool:
    return bool(_remote_base_url())


def _remote_headers(*, with_json: bool = False) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    token = str(os.environ.get(_SAFEBOX_REMOTE_TOKEN_ENV) or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if with_json:
        headers["Content-Type"] = "application/json"
    return headers


def _remote_json(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    base = _remote_base_url()
    if not base:
        raise RuntimeError("Safebox remote URL is not configured")
    body: bytes | None = None
    headers = _remote_headers(with_json=payload is not None)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{base}{path}", data=body, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        detail = raw.strip() or exc.reason
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            parsed = {"detail": detail}
        raise RemoteSafeboxError(
            f"Safebox remote {method.upper()} {path} failed: {parsed}",
            status_code=exc.code,
            payload=parsed if isinstance(parsed, dict) else {"detail": detail},
        ) from exc


def _remote_error_detail(exc: RemoteSafeboxError) -> dict[str, Any]:
    detail = exc.payload.get("detail")
    if isinstance(detail, dict):
        return detail
    if isinstance(detail, str) and detail.strip():
        return {"error": detail.strip()}
    return exc.payload if isinstance(exc.payload, dict) else {}


def _creative_credit_backend():
    from . import business_credits

    return business_credits


@contextmanager
def _creative_credit_conn(conn=None):
    if conn is not None:
        yield conn
        return
    from .runtime_app import resolve_database_url
    import psycopg

    raw_conn = psycopg.connect(
        resolve_database_url(),
        autocommit=True,
        prepare_threshold=None,
    )
    try:
        yield raw_conn
    finally:
        raw_conn.close()


def _balances_from_payload(payload: dict[str, Any], *, business_slug: str) -> CreativeCreditBalances:
    return CreativeCreditBalances(
        business_slug=str(payload.get("business_slug") or business_slug),
        balance_credits=int(payload.get("balance_credits") or 0),
        reserved_credits=int(payload.get("reserved_credits") or 0),
    )


def _reservation_from_payload(
    payload: dict[str, Any],
    *,
    reservation_key: str,
) -> CreativeCreditReservation:
    return CreativeCreditReservation(
        key=str(payload.get("key") or payload.get("reservation_key") or reservation_key),
        reserved_credits=int(payload.get("reserved_credits") or 0),
    )


def _local_open_business_credit_account(conn, business_slug: str) -> None:
    backend = _creative_credit_backend()
    with _creative_credit_conn(conn) as credit_conn:
        backend.open_business_credit_account(credit_conn, business_slug)


def _local_get_business_credit_balances(conn, business_slug: str) -> CreativeCreditBalances:
    backend = _creative_credit_backend()
    with _creative_credit_conn(conn) as credit_conn:
        backend.open_business_credit_account(credit_conn, business_slug)
        return backend.get_business_credit_balances(credit_conn, business_slug)


def _local_grant_credits(
    conn,
    business_slug: str,
    credits: int,
    idempotency_key: str,
    *,
    metadata: dict | None = None,
    stripe_ref: str | None = None,
) -> CreativeCreditBalances:
    backend = _creative_credit_backend()
    with _creative_credit_conn(conn) as credit_conn:
        backend.open_business_credit_account(credit_conn, business_slug)
        return backend.grant_credits(
            credit_conn,
            business_slug,
            credits,
            idempotency_key,
            metadata=metadata,
            stripe_ref=stripe_ref,
        )


def _local_reserve_credits(
    conn,
    business_slug: str,
    credits: int,
    reservation_key: str,
    *,
    metadata: dict | None = None,
) -> CreativeCreditReservation:
    backend = _creative_credit_backend()
    with _creative_credit_conn(conn) as credit_conn:
        backend.open_business_credit_account(credit_conn, business_slug)
        return backend.reserve_credits(
            credit_conn,
            business_slug,
            credits,
            reservation_key,
            metadata=metadata,
        )


def _local_commit_credits(
    conn,
    reservation_key: str,
    *,
    actual_credits: int | None = None,
    metadata: dict | None = None,
) -> CreativeCreditBalances:
    backend = _creative_credit_backend()
    with _creative_credit_conn(conn) as credit_conn:
        return backend.commit_credits(
            credit_conn,
            reservation_key,
            actual_credits=actual_credits,
            metadata=metadata,
        )


def _local_release_credits(
    conn,
    reservation_key: str,
    *,
    metadata: dict | None = None,
) -> CreativeCreditBalances:
    backend = _creative_credit_backend()
    with _creative_credit_conn(conn) as credit_conn:
        return backend.release_credits(
            credit_conn,
            reservation_key,
            metadata=metadata,
        )


def _require_sensitive(key: str) -> str:
    name = str(key or "").strip()
    _validate_env_key(name)
    if not is_sensitive_env_key(name):
        raise KeyError(f"safebox does not expose non-sensitive env key: {name}")
    return name


def _safebox_dir() -> Path:
    return Path(get_takyon_home()).expanduser() / _SAFEBOX_DIRNAME


def _user_api_keys_path() -> Path:
    return _safebox_dir() / _USER_API_KEYS_FILE_NAME


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _empty_user_api_key_state() -> dict[str, Any]:
    return {
        "version": _USER_API_KEYS_STATE_VERSION,
        "user_api_keys": {},
    }


def _load_user_api_key_state() -> dict[str, Any]:
    path = _user_api_keys_path()
    if not path.exists():
        return _empty_user_api_key_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - fail closed on corruption
        raise RuntimeError(f"invalid Safebox user-api-key registry at {path}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"invalid Safebox user-api-key registry at {path}")
    records = raw.get("user_api_keys")
    if records is None:
        raw["user_api_keys"] = {}
    elif not isinstance(records, dict):
        raise RuntimeError(f"invalid Safebox user-api-key registry at {path}")
    raw.setdefault("version", _USER_API_KEYS_STATE_VERSION)
    return raw


def _write_user_api_key_state(state: dict[str, Any]) -> None:
    path = _user_api_keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(path)
    os.chmod(path, 0o600)


@contextmanager
def _locked_user_api_key_state(*, write: bool):
    lock_path = _user_api_keys_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _USER_API_KEYS_MUTEX:
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(
                    lock_file.fileno(),
                    fcntl.LOCK_EX if write else fcntl.LOCK_SH,
                )
            try:
                state = _load_user_api_key_state()
                yield state
                if write:
                    _write_user_api_key_state(state)
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _user_api_key_records(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = state.setdefault("user_api_keys", {})
    if not isinstance(records, dict):
        raise RuntimeError("invalid Safebox user-api-key registry")
    return records  # type: ignore[return-value]


def _normalize_user_api_key_record(record: dict[str, Any]) -> dict[str, str | None]:
    return {
        "id": str(record.get("id") or "").strip(),
        "user_id": str(record.get("user_id") or "").strip(),
        "key_hash": str(record.get("key_hash") or "").strip(),
        "prefix": str(record.get("prefix") or "").strip(),
        "created_at": str(record.get("created_at") or "").strip(),
        "revoked_at": str(record.get("revoked_at") or "").strip() or None,
    }


def register_user_api_key(
    user_id: str,
    raw_key: str,
    *,
    key_id: str,
    created_at: str | None = None,
) -> dict[str, str | None]:
    """Register a top-level ``tk_...`` API key in Safebox."""
    if _remote_enabled():
        payload = _remote_json(
            "POST",
            "/v1/user-api-keys/register",
            {
                "user_id": str(user_id or "").strip(),
                "raw_key": str(raw_key or ""),
                "key_id": str(key_id or "").strip(),
                "created_at": str(created_at or "").strip() or None,
            },
        )
        record = payload.get("record")
        if not isinstance(record, dict):
            raise RuntimeError("Safebox remote register_user_api_key returned no record")
        return record
    owner = str(user_id or "").strip()
    if not owner:
        raise ValueError("missing user_id")
    key_ref = str(key_id or "").strip()
    if not key_ref:
        raise ValueError("missing key_id")
    if not is_well_formed(raw_key):
        raise ValueError("malformed api key")
    record = {
        "id": key_ref,
        "user_id": owner,
        "key_hash": hash_api_key(raw_key),
        "prefix": key_prefix(raw_key),
        "created_at": str(created_at or _utc_now_iso()),
        "revoked_at": None,
    }
    with _locked_user_api_key_state(write=True) as state:
        records = _user_api_key_records(state)
        for existing in records.values():
            normalized = _normalize_user_api_key_record(existing)
            if (
                normalized["user_id"] == owner
                and normalized["id"] != key_ref
                and normalized["revoked_at"] is None
            ):
                raise ValueError("active user api key already exists in Safebox")
        records[key_ref] = record
    return record


def resolve_user_api_key(raw_key: str) -> dict[str, str | None] | None:
    """Resolve a presented raw ``tk_...`` key against the Safebox registry."""
    if _remote_enabled():
        payload = _remote_json(
            "POST",
            "/v1/user-api-keys/resolve",
            {"raw_key": str(raw_key or "")},
        )
        record = payload.get("record")
        return record if isinstance(record, dict) else None
    if not is_well_formed(raw_key):
        return None
    presented_hash = hash_api_key(raw_key)
    with _locked_user_api_key_state(write=False) as state:
        for existing in _user_api_key_records(state).values():
            normalized = _normalize_user_api_key_record(existing)
            if normalized["revoked_at"] is not None:
                continue
            if normalized["key_hash"] == presented_hash:
                return normalized
    return None


def revoke_user_api_key(key_id: str, *, revoked_at: str | None = None) -> bool:
    """Revoke one Safebox-owned top-level API key by id."""
    if _remote_enabled():
        payload = _remote_json(
            "POST",
            "/v1/user-api-keys/revoke",
            {
                "key_id": str(key_id or "").strip(),
                "revoked_at": str(revoked_at or "").strip() or None,
            },
        )
        return bool(payload.get("revoked"))
    key_ref = str(key_id or "").strip()
    if not key_ref:
        return False
    changed = False
    with _locked_user_api_key_state(write=True) as state:
        record = _user_api_key_records(state).get(key_ref)
        if isinstance(record, dict) and not record.get("revoked_at"):
            record["revoked_at"] = str(revoked_at or _utc_now_iso())
            changed = True
    return changed


def revoke_user_api_keys_for_user(
    user_id: str,
    *,
    revoked_at: str | None = None,
) -> list[str]:
    """Revoke every active top-level API key owned by one Takyon user."""
    if _remote_enabled():
        payload = _remote_json(
            "POST",
            "/v1/user-api-keys/revoke-for-user",
            {
                "user_id": str(user_id or "").strip(),
                "revoked_at": str(revoked_at or "").strip() or None,
            },
        )
        revoked = payload.get("revoked_ids")
        return [str(item) for item in revoked] if isinstance(revoked, list) else []
    owner = str(user_id or "").strip()
    if not owner:
        return []
    changed: list[str] = []
    stamp = str(revoked_at or _utc_now_iso())
    with _locked_user_api_key_state(write=True) as state:
        for key_id, record in _user_api_key_records(state).items():
            if not isinstance(record, dict):
                continue
            normalized = _normalize_user_api_key_record(record)
            if normalized["user_id"] != owner or normalized["revoked_at"] is not None:
                continue
            record["revoked_at"] = stamp
            changed.append(str(key_id))
    return changed


def restore_user_api_keys(key_ids: list[str]) -> None:
    """Undo a staged revoke when an outer transactional caller rolls back."""
    if _remote_enabled():
        _remote_json("POST", "/v1/user-api-keys/restore", {"key_ids": list(key_ids or [])})
        return
    wanted = {
        str(key_id or "").strip()
        for key_id in key_ids
        if str(key_id or "").strip()
    }
    if not wanted:
        return
    with _locked_user_api_key_state(write=True) as state:
        for key_id, record in _user_api_key_records(state).items():
            if key_id in wanted and isinstance(record, dict):
                record["revoked_at"] = None


def delete_user_api_key(key_id: str) -> bool:
    """Delete one Safebox key record outright for transactional cleanup."""
    if _remote_enabled():
        payload = _remote_json(
            "DELETE",
            f"/v1/user-api-keys/{urllib.parse.quote(str(key_id or '').strip(), safe='')}",
        )
        return bool(payload.get("deleted"))
    key_ref = str(key_id or "").strip()
    if not key_ref:
        return False
    deleted = False
    with _locked_user_api_key_state(write=True) as state:
        records = _user_api_key_records(state)
        deleted = records.pop(key_ref, None) is not None
    return deleted


def open_business_credit_account(conn, business_slug: str) -> None:
    """Ensure one business creative-credit account exists inside Safebox authority."""
    slug = str(business_slug or "").strip()
    if not slug:
        raise ValueError("missing business_slug")
    if _remote_enabled():
        _remote_json(
            "POST",
            "/v1/creative-credits/accounts/open",
            {"business_slug": slug},
        )
        return
    _local_open_business_credit_account(conn, slug)


def get_business_credit_balances(conn, business_slug: str) -> CreativeCreditBalances:
    """Read one business creative-credit balance through Safebox authority."""
    slug = str(business_slug or "").strip()
    if not slug:
        raise ValueError("missing business_slug")
    if _remote_enabled():
        payload = _remote_json(
            "GET",
            f"/v1/creative-credits/{urllib.parse.quote(slug, safe='')}",
        )
        return _balances_from_payload(payload, business_slug=slug)
    return _local_get_business_credit_balances(conn, slug)


def grant_credits(
    conn,
    business_slug: str,
    credits: int,
    idempotency_key: str,
    *,
    metadata: dict | None = None,
    stripe_ref: str | None = None,
) -> CreativeCreditBalances:
    """Grant purchased business creative credits through Safebox authority."""
    slug = str(business_slug or "").strip()
    if not slug:
        raise ValueError("missing business_slug")
    if _remote_enabled():
        payload = _remote_json(
            "POST",
            "/v1/creative-credits/grant",
            {
                "business_slug": slug,
                "credits": int(credits),
                "idempotency_key": str(idempotency_key or "").strip(),
                "metadata": metadata or {},
                "stripe_ref": str(stripe_ref or "").strip() or None,
            },
        )
        return _balances_from_payload(payload, business_slug=slug)
    return _local_grant_credits(
        conn,
        slug,
        credits,
        idempotency_key,
        metadata=metadata,
        stripe_ref=stripe_ref,
    )


def reserve_credits(
    conn,
    business_slug: str,
    credits: int,
    reservation_key: str,
    *,
    metadata: dict | None = None,
) -> CreativeCreditReservation:
    """Reserve business creative credits through Safebox authority."""
    slug = str(business_slug or "").strip()
    key = str(reservation_key or "").strip()
    if not slug:
        raise ValueError("missing business_slug")
    if _remote_enabled():
        try:
            payload = _remote_json(
                "POST",
                "/v1/creative-credits/reserve",
                {
                    "business_slug": slug,
                    "credits": int(credits),
                    "reservation_key": key,
                    "metadata": metadata or {},
                },
            )
        except RemoteSafeboxError as exc:
            detail = _remote_error_detail(exc)
            if exc.status_code == 402:
                raise InsufficientCreativeCredits(
                    requested_credits=int(detail.get("requested_credits") or credits),
                    available_credits=int(detail.get("available_credits") or 0),
                ) from exc
            raise
        return _reservation_from_payload(payload, reservation_key=key)
    return _local_reserve_credits(
        conn,
        slug,
        credits,
        key,
        metadata=metadata,
    )


def commit_credits(
    conn,
    reservation_key: str,
    *,
    actual_credits: int | None = None,
    metadata: dict | None = None,
) -> CreativeCreditBalances:
    """Commit one business creative-credit reservation through Safebox authority."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    if _remote_enabled():
        try:
            payload = _remote_json(
                "POST",
                "/v1/creative-credits/commit",
                {
                    "reservation_key": key,
                    "actual_credits": (
                        None if actual_credits is None else int(actual_credits)
                    ),
                    "metadata": metadata or {},
                },
            )
        except RemoteSafeboxError as exc:
            if exc.status_code == 404:
                raise UnknownCreativeCreditReservation(key) from exc
            raise
        return _balances_from_payload(payload, business_slug="")
    return _local_commit_credits(
        conn,
        key,
        actual_credits=actual_credits,
        metadata=metadata,
    )


def release_credits(
    conn,
    reservation_key: str,
    *,
    metadata: dict | None = None,
) -> CreativeCreditBalances:
    """Release one business creative-credit reservation through Safebox authority."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    if _remote_enabled():
        try:
            payload = _remote_json(
                "POST",
                "/v1/creative-credits/release",
                {
                    "reservation_key": key,
                    "metadata": metadata or {},
                },
            )
        except RemoteSafeboxError as exc:
            if exc.status_code == 404:
                raise UnknownCreativeCreditReservation(key) from exc
            raise
        return _balances_from_payload(payload, business_slug="")
    return _local_release_credits(
        conn,
        key,
        metadata=metadata,
    )


def read_env_backed_value(key: str) -> str:
    """Read one sensitive env-backed value from env or TAKYON_HOME/.env."""
    if _remote_enabled():
        payload = _remote_json(
            "GET",
            f"/v1/env/{urllib.parse.quote(_require_sensitive(key), safe='')}",
        )
        return str(payload.get("value") or "").strip()
    name = _require_sensitive(key)
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    return str(load_env().get(name) or "").strip()


def first_env_backed_value(*keys: str) -> str:
    """Return the first non-empty sensitive value across env-backed aliases."""
    if _remote_enabled():
        payload = _remote_json(
            "POST",
            "/v1/env/first",
            {"keys": [str(key or "").strip() for key in keys]},
        )
        return str(payload.get("value") or "").strip()
    for key in keys:
        value = read_env_backed_value(key)
        if value:
            return value
    return ""


def save_env_backed_value(key: str, value: str) -> None:
    """Persist one sensitive env-backed value through the Safebox authority."""
    if _remote_enabled():
        _remote_json(
            "POST",
            f"/v1/env/{urllib.parse.quote(_require_sensitive(key), safe='')}",
            {"value": value},
        )
        return
    if is_managed():
        managed_error(f"set {key}")
        return
    name = _require_sensitive(key)
    _save_env_value_direct(name, _normalize_env_value_for_storage(name, value))


def remove_env_backed_value(key: str) -> bool:
    """Remove one sensitive env-backed value through the Safebox authority."""
    if _remote_enabled():
        payload = _remote_json(
            "DELETE",
            f"/v1/env/{urllib.parse.quote(_require_sensitive(key), safe='')}",
        )
        return bool(payload.get("removed"))
    if is_managed():
        managed_error(f"remove {key}")
        return False
    name = _require_sensitive(key)
    return _remove_env_value_direct(name)


def sensitive_env_snapshot() -> Dict[str, str]:
    """Return the merged env-backed sensitive-key snapshot."""
    if _remote_enabled():
        payload = _remote_json("GET", "/v1/env/snapshot")
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, dict):
            return {}
        return {
            str(key or "").strip(): str(value or "").strip()
            for key, value in snapshot.items()
            if str(key or "").strip()
        }
    snapshot = {
        key: str(value or "").strip()
        for key, value in load_env().items()
        if is_sensitive_env_key(key)
    }
    for key, value in os.environ.items():
        if is_sensitive_env_key(key):
            snapshot[key] = str(value or "").strip()
    return snapshot


def list_env_backed_keys(*, sensitive_only: bool = True) -> list[str]:
    """List env-backed keys known to Safebox."""
    if _remote_enabled():
        flag = "1" if sensitive_only else "0"
        payload = _remote_json("GET", f"/v1/env?{urllib.parse.urlencode({'sensitive_only': flag})}")
        keys = payload.get("keys")
        return [str(item or "").strip() for item in keys] if isinstance(keys, list) else []
    if sensitive_only:
        return sorted(sensitive_env_snapshot().keys())
    merged = {key: value for key, value in load_env().items()}
    for key, value in os.environ.items():
        merged[key] = value
    return sorted(str(key or "").strip() for key in merged.keys() if str(key or "").strip())
