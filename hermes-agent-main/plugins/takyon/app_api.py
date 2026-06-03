"""Hermes-native business app auth/billing HTTP API."""

from __future__ import annotations

import json
import uuid
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Provider call + cost logic now lives in the shared ai_provider leaf (one implementation for both
# this SQLite route and the Postgres AI gateway). Bound to the original private names so this
# module's body — and its test — are unchanged.
from .ai_provider import (
    AnthropicPricingUnavailable,
    anthropic_key as _anthropic_key,
    anthropic_payload as _anthropic_payload,
    anthropic_rates_microusd_per_token as _anthropic_rates_microusd_per_token,
    anthropic_text as _anthropic_text,
    call_anthropic as _call_anthropic,
    microusd_cost as _microusd_cost,
)
from .core import (
    TakyonStore,
    handle_business_create_app_checkout,
    handle_business_read_app_account,
    handle_business_record_app_usage,
    handle_business_record_stripe_webhook,
    handle_business_request_app_magic_link,
    handle_business_verify_app_magic_link,
    load_takyon_env,
)


SESSION_COOKIE = "takyon_app_session"


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("content-length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    return json.loads(raw)


def _tool(raw: str) -> tuple[int, dict]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"success": False, "error": str(exc)}
    if payload.get("success") is False or payload.get("error"):
        return HTTPStatus.BAD_REQUEST, payload
    return HTTPStatus.OK, payload


def _app_budget_remaining_microusd(business: str) -> dict:
    app = TakyonStore().read(scope=f"business:{business}", query="app", limit=20).get("app") or {}
    budget = app.get("budget") or {}
    usage = app.get("usage_this_period") or {}
    hard_limit = int(budget.get("hard_limit_microusd") or 0)
    used_actual = int(usage.get("actual_cost_microusd") or 0)
    used_estimated = int(usage.get("estimated_cost_microusd") or 0)
    used = max(used_actual, used_estimated)
    return {
        "status": budget.get("status") or "missing",
        "hard_limit_microusd": hard_limit,
        "used_microusd": used,
        "remaining_microusd": hard_limit - used,
    }


def _cookie_session(handler: BaseHTTPRequestHandler) -> str:
    cookie = SimpleCookie(handler.headers.get("cookie") or "")
    morsel = cookie.get(SESSION_COOKIE)
    return morsel.value if morsel else ""


def _set_session_cookie(handler: BaseHTTPRequestHandler, token: str) -> None:
    secure = " Secure;" if handler.headers.get("x-forwarded-proto") == "https" else ""
    handler.send_header(
        "Set-Cookie",
        f"{SESSION_COOKIE}={token}; Max-Age={30 * 24 * 60 * 60}; Path=/; HttpOnly; SameSite=Lax;{secure}",
    )


def _app_route(parts: list[str]) -> tuple[str, list[str], bool] | None:
    if len(parts) >= 5 and parts[:3] == ["api", "takyon", "apps"]:
        return parts[3], parts[4:], False
    if len(parts) >= 4 and parts[:2] == ["api", "generated-apps"]:
        return parts[2], parts[3:], True
    return None


def _owner_token_on_app_plane(handler: BaseHTTPRequestHandler) -> bool:
    auth = str(handler.headers.get("authorization") or "").strip()
    return auth.startswith("Bearer tk_")


class TakyonAppApiHandler(BaseHTTPRequestHandler):
    server_version = "TakyonAppAPI/0.1"

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover - terminal runtime logging.
        print(f"[app-api] {self.address_string()} {fmt % args}")

    def _parts(self) -> tuple[list[str], dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return [part for part in parsed.path.split("/") if part], parse_qs(parsed.query)

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name.
        if _owner_token_on_app_plane(self):
            _json_response(self, HTTPStatus.FORBIDDEN, {"success": False, "error": "owner_token_rejected_on_app_plane"})
            return
        parts, query = self._parts()
        app_route = _app_route(parts)
        if app_route:
            business, route, _legacy = app_route
            if route == ["auth", "verify"]:
                token = (query.get("token") or [""])[0]
                status, payload = _tool(handle_business_verify_app_magic_link({"business": business, "token": token}))
                if status != HTTPStatus.OK:
                    _json_response(self, status, payload)
                    return
                redirect = (query.get("redirect") or ["/?signed_in=1"])[0]
                self.send_response(HTTPStatus.FOUND)
                _set_session_cookie(self, str(payload["session_token"]))
                self.send_header("Location", redirect)
                self.end_headers()
                return
            if route == ["session"] or route == ["account"]:
                token = _cookie_session(self)
                if not token:
                    _json_response(self, HTTPStatus.OK, {"success": True, "authenticated": False})
                    return
                status, payload = _tool(handle_business_read_app_account({"business": business, "session_token": token}))
                payload["authenticated"] = status == HTTPStatus.OK
                _json_response(self, status, payload)
                return
        _json_response(self, HTTPStatus.NOT_FOUND, {"success": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name.
        if _owner_token_on_app_plane(self):
            _json_response(self, HTTPStatus.FORBIDDEN, {"success": False, "error": "owner_token_rejected_on_app_plane"})
            return
        parts, _query = self._parts()
        if parts == ["api", "webhooks", "stripe"]:
            length = int(self.headers.get("content-length") or 0)
            raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
            status, payload = _tool(handle_business_record_stripe_webhook({
                "raw_body": raw_body,
                "stripe_signature": self.headers.get("stripe-signature") or "",
            }))
            _json_response(self, status, payload)
            return

        app_route = _app_route(parts)
        if app_route:
            business, route, _legacy = app_route
            body = _read_json(self)
            if route == ["auth", "request"]:
                host = self.headers.get("x-forwarded-host") or self.headers.get("host") or ""
                proto = self.headers.get("x-forwarded-proto") or "http"
                origin = body.get("origin") or (f"{proto}://{host}" if host else "")
                status, payload = _tool(handle_business_request_app_magic_link({
                    "business": business,
                    "email": body.get("email"),
                    "name": body.get("name"),
                    "origin": origin,
                    "app_slug": body.get("app_slug") or business,
                    "product_name": body.get("product_name") or business,
                    "send_email": bool(body.get("send_email", True)),
                }))
                if not body.get("return_token"):
                    payload.pop("token", None)
                _json_response(self, status, payload)
                return
            if route == ["checkout"]:
                token = _cookie_session(self)
                account = {}
                if token:
                    _account_status, account = _tool(handle_business_read_app_account({"business": business, "session_token": token}))
                status, payload = _tool(handle_business_create_app_checkout({
                    "business": business,
                    "plan_key": body.get("plan_key") or body.get("planKey"),
                    "success_url": body.get("success_url") or body.get("successUrl"),
                    "cancel_url": body.get("cancel_url") or body.get("cancelUrl"),
                    "customer_email": body.get("customer_email") or body.get("customerEmail") or (account.get("user") or {}).get("email"),
                    "app_user_id": (account.get("user") or {}).get("id"),
                    "metadata": body.get("metadata") or {},
                }))
                _json_response(self, status, payload)
                return
            if route == ["usage"]:
                token = _cookie_session(self)
                if not token:
                    _json_response(self, HTTPStatus.UNAUTHORIZED, {"success": False, "error": "missing app session"})
                    return
                account_status, account = _tool(handle_business_read_app_account({"business": business, "session_token": token}))
                if account_status != HTTPStatus.OK:
                    _json_response(self, account_status, account)
                    return
                user = account.get("user") or {}
                status, payload = _tool(handle_business_record_app_usage({
                    "business": business,
                    "app_user_id": user.get("id"),
                    "app_user_tier": user.get("tier"),
                    "purpose": body.get("purpose") or "product_usage",
                    "route": body.get("route") or self.path,
                    "status": body.get("status") or "completed",
                    "estimated_cost_microusd": body.get("estimated_cost_microusd") or body.get("estimatedCostMicrousd") or 0,
                    "actual_cost_microusd": body.get("actual_cost_microusd") or body.get("actualCostMicrousd") or 0,
                    "input_tokens": body.get("input_tokens") or body.get("inputTokens"),
                    "output_tokens": body.get("output_tokens") or body.get("outputTokens"),
                    "provider_request_id": body.get("provider_request_id") or body.get("providerRequestId"),
                    "provider": body.get("provider"),
                    "model": body.get("model"),
                    "metadata": body.get("metadata") or {},
                    "idempotency_key": body.get("idempotency_key") or body.get("idempotencyKey") or f"usage:{business}:{user.get('id')}:{body.get('providerRequestId') or body.get('provider_request_id') or self.log_date_time_string()}",
                }))
                _json_response(self, status, payload)
                return
            if route == ["generate"]:
                _json_response(
                    self,
                    HTTPStatus.GONE,
                    {
                        "success": False,
                        "error": (
                            "legacy standalone app generate route retired; "
                            "use the dashboard runtime authority instead"
                        ),
                    },
                )
                return
        _json_response(self, HTTPStatus.NOT_FOUND, {"success": False, "error": "not found"})


def run_app_api_server(host: str = "127.0.0.1", port: int = 8787) -> None:
    raise RuntimeError(
        "legacy standalone app API server retired; use `takyon dashboard` for app-runtime rails"
    )
