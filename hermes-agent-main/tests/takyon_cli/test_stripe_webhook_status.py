from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request

from plugins.takyon import core, safebox
from takyon_cli import web_server
from tools.registry import tool_error


@pytest.mark.parametrize(
    "failure",
    [
        safebox.StripeAppWebhookUnconfigured("missing"),
        safebox.SafeboxAuthorityUnavailable("unavailable"),
        safebox.ManagedSecretLookupError("secret dependency unavailable"),
        safebox.RemoteSafeboxError("upstream", status_code=504, payload={}),
    ],
)
def test_core_preserves_retryable_safebox_webhook_failures(monkeypatch, failure):
    monkeypatch.setattr(core, "_store", lambda: object())
    monkeypatch.setattr(core, "_db_backend", lambda: "postgres")

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(safebox, "process_stripe_app_webhook", fail)
    payload = json.loads(
        core.handle_business_record_stripe_webhook(
            {"raw_body": "{}", "stripe_signature": "t=1,v1=bad"}
        )
    )

    assert payload["success"] is False
    assert payload["error_code"] == "stripe_webhook_unavailable"
    assert payload["retryable"] is True


def test_core_keeps_invalid_stripe_signature_non_retryable(monkeypatch):
    monkeypatch.setattr(core, "_store", lambda: object())
    monkeypatch.setattr(core, "_db_backend", lambda: "postgres")

    def fail(*_args, **_kwargs):
        raise safebox.StripeAppWebhookInvalidSignature("bad")

    monkeypatch.setattr(safebox, "process_stripe_app_webhook", fail)
    payload = json.loads(
        core.handle_business_record_stripe_webhook(
            {"raw_body": "{}", "stripe_signature": "t=1,v1=bad"}
        )
    )

    assert payload["error_code"] == "stripe_webhook_invalid_signature"
    assert payload.get("retryable") is not True


def _request() -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": b"{}", "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/webhooks/stripe",
            "headers": [(b"stripe-signature", b"t=1,v1=bad")],
        },
        receive,
    )


@pytest.mark.parametrize(
    ("tool_payload", "expected_status"),
    [
        (
            tool_error(
                "Stripe webhook authority unavailable",
                success=False,
                error_code="stripe_webhook_unavailable",
                retryable=True,
            ),
            503,
        ),
        (
            tool_error(
                "Stripe signature verification failed",
                success=False,
                error_code="stripe_webhook_invalid_signature",
            ),
            400,
        ),
    ],
)
def test_public_stripe_webhook_status_preserves_retryability(
    monkeypatch, tool_payload, expected_status
):
    monkeypatch.setattr(
        web_server,
        "handle_business_record_stripe_webhook",
        lambda _args: tool_payload,
    )

    response = asyncio.run(web_server.takyon_app_stripe_webhook(_request()))

    assert response.status_code == expected_status
