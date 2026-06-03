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


def is_sensitive_env_key(key: str) -> bool:
    name = str(key or "").strip()
    if not name:
        return False
    if name in _EXACT_SENSITIVE_ENV_KEYS:
        return True
    return name.endswith(_SENSITIVE_ENV_SUFFIXES)


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
    key_ref = str(key_id or "").strip()
    if not key_ref:
        return False
    deleted = False
    with _locked_user_api_key_state(write=True) as state:
        records = _user_api_key_records(state)
        deleted = records.pop(key_ref, None) is not None
    return deleted


def read_env_backed_value(key: str) -> str:
    """Read one sensitive env-backed value from env or TAKYON_HOME/.env."""
    name = _require_sensitive(key)
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    return str(load_env().get(name) or "").strip()


def first_env_backed_value(*keys: str) -> str:
    """Return the first non-empty sensitive value across env-backed aliases."""
    for key in keys:
        value = read_env_backed_value(key)
        if value:
            return value
    return ""


def save_env_backed_value(key: str, value: str) -> None:
    """Persist one sensitive env-backed value through the Safebox authority."""
    if is_managed():
        managed_error(f"set {key}")
        return
    name = _require_sensitive(key)
    _save_env_value_direct(name, _normalize_env_value_for_storage(name, value))


def remove_env_backed_value(key: str) -> bool:
    """Remove one sensitive env-backed value through the Safebox authority."""
    if is_managed():
        managed_error(f"remove {key}")
        return False
    name = _require_sensitive(key)
    return _remove_env_value_direct(name)


def sensitive_env_snapshot() -> Dict[str, str]:
    """Return the merged env-backed sensitive-key snapshot."""
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
    if sensitive_only:
        return sorted(sensitive_env_snapshot().keys())
    merged = {key: value for key, value in load_env().items()}
    for key, value in os.environ.items():
        merged[key] = value
    return sorted(str(key or "").strip() for key in merged.keys() if str(key or "").strip())
