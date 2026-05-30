"""Hermes-native business app auth/billing HTTP API."""

from __future__ import annotations

import json
import os
import uuid
import urllib.error
import urllib.request
from decimal import Decimal, ROUND_CEILING
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

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
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


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


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def _anthropic_key() -> str:
    try:
        from takyon_cli.auth import get_anthropic_key

        return str(get_anthropic_key() or "").strip()
    except Exception:
        return _env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_TOKEN")


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(value))
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _anthropic_model(body: dict) -> str:
    return str(
        body.get("model")
        or _env("TAKYON_APP_ANTHROPIC_MODEL")
        or _env("ANTHROPIC_MODEL")
        or "claude-sonnet-4-6"
    ).strip()


def _anthropic_rates_microusd_per_token(model: str) -> tuple[Decimal, Decimal, str]:
    input_override = _env("TAKYON_APP_ANTHROPIC_INPUT_MICROUSD_PER_TOKEN")
    output_override = _env("TAKYON_APP_ANTHROPIC_OUTPUT_MICROUSD_PER_TOKEN")
    if input_override or output_override:
        return (
            Decimal(input_override or "3"),
            Decimal(output_override or "15"),
            "env",
        )

    lowered = model.lower()
    if "opus" in lowered:
        return Decimal("15"), Decimal("75"), "default-opus-estimate"
    if "haiku" in lowered:
        return Decimal("1"), Decimal("5"), "default-haiku-estimate"
    return Decimal("3"), Decimal("15"), "default-sonnet-estimate"


def _microusd_cost(model: str, input_tokens: int, output_tokens: int) -> int:
    input_rate, output_rate, _source = _anthropic_rates_microusd_per_token(model)
    total = input_rate * Decimal(max(0, input_tokens)) + output_rate * Decimal(max(0, output_tokens))
    return int(total.to_integral_value(rounding=ROUND_CEILING))


def _estimate_input_tokens(messages: list[dict], system: str) -> int:
    text_parts = [system]
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text") or ""))
    return max(1, sum(len(part) for part in text_parts) // 4)


def _anthropic_payload(body: dict) -> tuple[dict, str, int]:
    model = _anthropic_model(body)
    max_tokens = _bounded_int(
        body.get("max_tokens") or body.get("maxTokens"),
        default=1024,
        minimum=1,
        maximum=_bounded_int(_env("TAKYON_APP_ANTHROPIC_MAX_TOKENS", "4096"), default=4096, minimum=1, maximum=200_000),
    )
    raw_messages = body.get("messages")
    messages: list[dict] = []
    if isinstance(raw_messages, list):
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user").strip()
            if role not in {"user", "assistant"}:
                role = "user"
            content = item.get("content")
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                text_items = [
                    {"type": "text", "text": str(part.get("text") or "")}
                    for part in content
                    if isinstance(part, dict) and str(part.get("text") or "").strip()
                ]
                if text_items:
                    messages.append({"role": role, "content": text_items})
    if not messages:
        prompt = str(body.get("prompt") or body.get("input") or "").strip()
        if not prompt:
            raise ValueError("prompt or messages is required")
        messages = [{"role": "user", "content": prompt}]

    system = str(body.get("system") or "").strip()
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        payload["system"] = system
    if body.get("temperature") is not None:
        payload["temperature"] = max(0.0, min(1.0, float(body.get("temperature") or 0)))
    return payload, model, _estimate_input_tokens(messages, system)


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


def _call_anthropic(payload: dict, api_key: str) -> dict:
    request = urllib.request.Request(
        ANTHROPIC_MESSAGES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
            "x-api-key": api_key,
        },
        method="POST",
    )
    timeout = _bounded_int(_env("TAKYON_APP_ANTHROPIC_TIMEOUT_SECONDS", "60"), default=60, minimum=5, maximum=300)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic API returned {exc.code}: {body[:500]}") from exc


def _anthropic_text(response: dict) -> str:
    parts: list[str] = []
    for item in response.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part)


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


class TakyonAppApiHandler(BaseHTTPRequestHandler):
    server_version = "TakyonAppAPI/0.1"

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover - terminal runtime logging.
        print(f"[app-api] {self.address_string()} {fmt % args}")

    def _parts(self) -> tuple[list[str], dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return [part for part in parsed.path.split("/") if part], parse_qs(parsed.query)

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name.
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
                token = _cookie_session(self)
                if not token:
                    _json_response(self, HTTPStatus.UNAUTHORIZED, {"success": False, "error": "missing app session"})
                    return
                account_status, account = _tool(handle_business_read_app_account({"business": business, "session_token": token}))
                if account_status != HTTPStatus.OK:
                    _json_response(self, account_status, account)
                    return
                user = account.get("user") or {}
                try:
                    anthropic_payload, model, estimated_input_tokens = _anthropic_payload(body)
                except Exception as exc:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"success": False, "error": str(exc)})
                    return
                estimated_output_tokens = int(anthropic_payload.get("max_tokens") or 0)
                estimated_cost = int(
                    body.get("estimated_cost_microusd")
                    or body.get("estimatedCostMicrousd")
                    or _microusd_cost(model, estimated_input_tokens, estimated_output_tokens)
                )
                budget = _app_budget_remaining_microusd(business)
                if budget["status"] != "active":
                    _json_response(self, HTTPStatus.PAYMENT_REQUIRED, {"success": False, "error": "app budget is not active", "budget": budget})
                    return
                if estimated_cost > int(budget["remaining_microusd"]):
                    _json_response(
                        self,
                        HTTPStatus.PAYMENT_REQUIRED,
                        {
                            "success": False,
                            "error": "app usage would exceed budget cap",
                            "estimated_cost_microusd": estimated_cost,
                            "budget": budget,
                        },
                    )
                    return
                api_key = _anthropic_key()
                if not api_key:
                    _json_response(self, HTTPStatus.FAILED_DEPENDENCY, {"success": False, "error": "missing Anthropic API credential"})
                    return
                provider_request_id = ""
                try:
                    provider_response = _call_anthropic(anthropic_payload, api_key)
                    provider_request_id = str(provider_response.get("id") or "")
                    usage = provider_response.get("usage") or {}
                    input_tokens = int(usage.get("input_tokens") or estimated_input_tokens)
                    output_tokens = int(usage.get("output_tokens") or 0)
                    actual_cost = _microusd_cost(model, input_tokens, output_tokens)
                    status, usage_payload = _tool(handle_business_record_app_usage({
                        "business": business,
                        "app_user_id": user.get("id"),
                        "app_user_tier": user.get("tier"),
                        "purpose": body.get("purpose") or "ai_generate",
                        "route": f"/api/takyon/apps/{business}/generate",
                        "status": "completed",
                        "estimated_cost_microusd": estimated_cost,
                        "actual_cost_microusd": actual_cost,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "provider_request_id": provider_request_id,
                        "provider": "anthropic",
                        "model": model,
                        "metadata": {
                            "cost_rate_source": _anthropic_rates_microusd_per_token(model)[2],
                            "request_metadata": body.get("metadata") or {},
                        },
                        "idempotency_key": body.get("idempotency_key") or body.get("idempotencyKey") or f"generate:{business}:{user.get('id')}:{provider_request_id or uuid.uuid4().hex}",
                    }))
                    if status != HTTPStatus.OK:
                        _json_response(self, status, usage_payload)
                        return
                    _json_response(
                        self,
                        HTTPStatus.OK,
                        {
                            "success": True,
                            "text": _anthropic_text(provider_response),
                            "content": provider_response.get("content") or [],
                            "model": model,
                            "usage": {
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "estimated_cost_microusd": estimated_cost,
                                "actual_cost_microusd": actual_cost,
                            },
                            "receipt": usage_payload,
                        },
                    )
                    return
                except Exception as exc:
                    _tool(handle_business_record_app_usage({
                        "business": business,
                        "app_user_id": user.get("id"),
                        "app_user_tier": user.get("tier"),
                        "purpose": body.get("purpose") or "ai_generate",
                        "route": f"/api/takyon/apps/{business}/generate",
                        "status": "failed",
                        "estimated_cost_microusd": estimated_cost,
                        "actual_cost_microusd": 0,
                        "provider_request_id": provider_request_id,
                        "provider": "anthropic",
                        "model": model,
                        "error": str(exc),
                        "metadata": {"request_metadata": body.get("metadata") or {}},
                        "idempotency_key": body.get("idempotency_key") or body.get("idempotencyKey") or f"generate-failed:{business}:{user.get('id')}:{uuid.uuid4().hex}",
                    }))
                    _json_response(self, HTTPStatus.BAD_GATEWAY, {"success": False, "error": str(exc)})
                    return
        _json_response(self, HTTPStatus.NOT_FOUND, {"success": False, "error": "not found"})


def run_app_api_server(host: str = "127.0.0.1", port: int = 8787) -> None:
    load_takyon_env()
    server = ThreadingHTTPServer((host, int(port)), TakyonAppApiHandler)
    print(f"Takyon app API listening on http://{host}:{port}")
    print("Routes: /api/takyon/apps/<business>/auth/request, /auth/verify, /session, /account, /checkout, /usage, /generate, /api/webhooks/stripe")
    print("Compatibility route also accepted: /api/generated-apps/<business>/...")
    server.serve_forever()
