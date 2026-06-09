"""Dedicated Safebox service app.

This is the service boundary for Safebox when it runs on its own VPS. The
runtime planes talk to it over HTTP; the service itself still uses the local
Safebox authority module as the single backing implementation.
"""

from __future__ import annotations

import hmac
import json
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


class _OpenCreativeCreditAccountBody(BaseModel):
    business_slug: str


class _GrantCreativeCreditsBody(BaseModel):
    business_slug: str
    credits: int
    idempotency_key: str
    metadata: dict[str, Any] | None = None
    stripe_ref: str | None = None


class _CreativeCreditCheckoutBody(BaseModel):
    user_id: str
    business_slug: str
    credits: int | None = None
    pack_id: str | None = None
    success_url: str
    cancel_url: str


class _ReserveCreativeCreditsBody(BaseModel):
    business_slug: str
    credits: int
    reservation_key: str
    metadata: dict[str, Any] | None = None


class _CommitCreativeCreditsBody(BaseModel):
    reservation_key: str
    actual_credits: int | None = None
    metadata: dict[str, Any] | None = None


class _ReleaseCreativeCreditsBody(BaseModel):
    reservation_key: str
    metadata: dict[str, Any] | None = None


class _StripeBillingWebhookVerifyBody(BaseModel):
    raw_body: str
    signature: str


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

    @app.post("/v1/creative-credits/accounts/open")
    def open_creative_credit_account(
        body: _OpenCreativeCreditAccountBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        safebox._local_open_business_credit_account(None, body.business_slug)
        return {"ok": True}

    @app.get("/v1/creative-credits/{business_slug}")
    def get_creative_credit_balances(
        business_slug: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        balances = safebox._local_get_business_credit_balances(None, business_slug)
        return {
            "business_slug": balances.business_slug,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
        }

    @app.post("/v1/creative-credits/checkout")
    def create_creative_credit_checkout(
        body: _CreativeCreditCheckoutBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        from . import stripe_util
        from .control_api import create_creative_credit_checkout_session

        try:
            session, charge = create_creative_credit_checkout_session(
                body.user_id,
                body.business_slug,
                credits=body.credits,
                pack_id=body.pack_id,
                success_url=body.success_url,
                cancel_url=body.cancel_url,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="unknown_credit_pack") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except stripe_util.StripeError as exc:
            message = str(exc)
            if "STRIPE_SECRET_KEY" in message or "creative_credit_checkout_unconfigured" in message:
                raise HTTPException(
                    status_code=503, detail="creative_credit_checkout_unconfigured"
                ) from exc
            raise HTTPException(status_code=502, detail=f"stripe_error: {message}") from exc
        return {
            "checkout_url": session.get("url"),
            "session_id": session.get("id"),
            "business_slug": body.business_slug,
            "pack_id": charge.get("pack_id"),
            "credits": charge["credits"],
            "amount_cents": charge["amount_cents"],
            "price_cents_per_credit": charge.get("price_cents_per_credit"),
        }

    @app.post("/v1/creative-credits/grant")
    def grant_creative_credits(
        body: _GrantCreativeCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        balances = safebox._local_grant_credits(
            None,
            body.business_slug,
            body.credits,
            body.idempotency_key,
            metadata=body.metadata,
            stripe_ref=body.stripe_ref,
        )
        return {
            "business_slug": balances.business_slug,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
        }

    @app.post("/v1/stripe/billing-webhook/verify")
    def verify_stripe_billing_webhook(
        body: _StripeBillingWebhookVerifyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        from . import stripe_util

        secret = safebox.read_env_backed_value("STRIPE_BILLING_WEBHOOK_SECRET")
        if not secret:
            raise HTTPException(status_code=503, detail="billing_webhook_unconfigured")
        try:
            stripe_util.verify_stripe_signature(body.raw_body, body.signature, secret)
        except stripe_util.StripeError as exc:
            raise HTTPException(status_code=400, detail="invalid_signature") from exc
        event = json.loads(body.raw_body)
        return {"event": event if isinstance(event, dict) else {}}

    @app.post("/v1/creative-credits/reserve")
    def reserve_creative_credits(
        body: _ReserveCreativeCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            reservation = safebox._local_reserve_credits(
                None,
                body.business_slug,
                body.credits,
                body.reservation_key,
                metadata=body.metadata,
            )
        except safebox.InsufficientCreativeCredits as exc:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": str(exc),
                    "requested_credits": exc.requested_credits,
                    "available_credits": exc.available_credits,
                },
            ) from exc
        return {
            "key": reservation.key,
            "reserved_credits": reservation.reserved_credits,
        }

    @app.post("/v1/creative-credits/commit")
    def commit_creative_credits(
        body: _CommitCreativeCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            balances = safebox._local_commit_credits(
                None,
                body.reservation_key,
                actual_credits=body.actual_credits,
                metadata=body.metadata,
            )
        except safebox.UnknownCreativeCreditReservation as exc:
            raise HTTPException(
                status_code=404,
                detail={"error": "unknown_creative_credit_reservation", "reservation_key": str(exc)},
            ) from exc
        return {
            "business_slug": balances.business_slug,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
        }

    @app.post("/v1/creative-credits/release")
    def release_creative_credits(
        body: _ReleaseCreativeCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            balances = safebox._local_release_credits(
                None,
                body.reservation_key,
                metadata=body.metadata,
            )
        except safebox.UnknownCreativeCreditReservation as exc:
            raise HTTPException(
                status_code=404,
                detail={"error": "unknown_creative_credit_reservation", "reservation_key": str(exc)},
            ) from exc
        return {
            "business_slug": balances.business_slug,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
        }

    return app


app = build_safebox_app()
