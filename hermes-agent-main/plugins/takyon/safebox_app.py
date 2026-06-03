"""Dedicated Safebox service app.

This is the service boundary for Safebox when it runs on its own VPS. The
runtime planes talk to it over HTTP; the service itself still uses the local
Safebox authority module as the single backing implementation.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from . import safebox

_SAFEBOX_TOKEN_ENV = "TAKYON_SAFEBOX_TOKEN"


class _EnvValueBody(BaseModel):
    value: str


class _FirstEnvBody(BaseModel):
    keys: list[str]


class _RegisterUserKeyBody(BaseModel):
    user_id: str
    raw_key: str
    key_id: str
    created_at: str | None = None


class _ResolveUserKeyBody(BaseModel):
    raw_key: str


class _RevokeUserKeyBody(BaseModel):
    key_id: str
    revoked_at: str | None = None


class _RevokeUserKeysForUserBody(BaseModel):
    user_id: str
    revoked_at: str | None = None


class _RestoreUserKeysBody(BaseModel):
    key_ids: list[str]


def _require_internal_token(authorization: str | None = Header(default=None)) -> None:
    expected = str(os.environ.get(_SAFEBOX_TOKEN_ENV) or "").strip()
    if not expected:
        return
    presented = str(authorization or "").strip()
    want = f"Bearer {expected}"
    if not hmac.compare_digest(presented.encode(), want.encode()):
        raise HTTPException(status_code=401, detail="unauthorized")


def build_safebox_app() -> FastAPI:
    app = FastAPI(title="Takyon Safebox")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/env/{key}")
    def read_env_value(key: str, authorization: str | None = Header(default=None)) -> dict[str, str]:
        _require_internal_token(authorization)
        return {"value": safebox.read_env_backed_value(key)}

    @app.post("/v1/env/first")
    def first_env_value(body: _FirstEnvBody, authorization: str | None = Header(default=None)) -> dict[str, str]:
        _require_internal_token(authorization)
        return {"value": safebox.first_env_backed_value(*body.keys)}

    @app.post("/v1/env/{key}")
    def save_env_value(key: str, body: _EnvValueBody, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        _require_internal_token(authorization)
        safebox.save_env_backed_value(key, body.value)
        return {"ok": True}

    @app.delete("/v1/env/{key}")
    def delete_env_value(key: str, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        _require_internal_token(authorization)
        return {"removed": safebox.remove_env_backed_value(key)}

    @app.get("/v1/env/snapshot")
    def env_snapshot(authorization: str | None = Header(default=None)) -> dict[str, dict[str, str]]:
        _require_internal_token(authorization)
        return {"snapshot": safebox.sensitive_env_snapshot()}

    @app.get("/v1/env")
    def env_keys(
        sensitive_only: str = Query(default="1"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, list[str]]:
        _require_internal_token(authorization)
        return {"keys": safebox.list_env_backed_keys(sensitive_only=sensitive_only != "0")}

    @app.post("/v1/user-api-keys/register")
    def register_user_key(
        body: _RegisterUserKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return {
            "record": safebox.register_user_api_key(
                body.user_id,
                body.raw_key,
                key_id=body.key_id,
                created_at=body.created_at,
            )
        }

    @app.post("/v1/user-api-keys/resolve")
    def resolve_user_key(
        body: _ResolveUserKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return {"record": safebox.resolve_user_api_key(body.raw_key)}

    @app.post("/v1/user-api-keys/revoke")
    def revoke_user_key(
        body: _RevokeUserKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        return {"revoked": safebox.revoke_user_api_key(body.key_id, revoked_at=body.revoked_at)}

    @app.post("/v1/user-api-keys/revoke-for-user")
    def revoke_user_keys_for_user(
        body: _RevokeUserKeysForUserBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, list[str]]:
        _require_internal_token(authorization)
        return {
            "revoked_ids": safebox.revoke_user_api_keys_for_user(
                body.user_id,
                revoked_at=body.revoked_at,
            )
        }

    @app.post("/v1/user-api-keys/restore")
    def restore_user_keys(
        body: _RestoreUserKeysBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        safebox.restore_user_api_keys(body.key_ids)
        return {"ok": True}

    @app.delete("/v1/user-api-keys/{key_id}")
    def delete_user_key(key_id: str, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        _require_internal_token(authorization)
        return {"deleted": safebox.delete_user_api_key(key_id)}

    return app


app = build_safebox_app()
