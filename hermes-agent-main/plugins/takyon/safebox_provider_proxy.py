"""Operator/platform PROVIDER PROXY routes on the safebox service app — AUTHORITATIVELY MONEY-GATED.

This is the operator/platform counterpart to the business-scoped capability broker at ``/v1/providers/*``
(see ``safebox_app.py``). Where that broker meters PRODUCT (sub-user) spend, THIS proxy meters the
operator/platform plane — the CEO agent + coding worker + platform ``web_tools`` calling Anthropic /
Tavily with the stock SDK against a STATIC key. Its purpose is twofold:

  1. Keyless egress: the safebox resolves the real provider key LOCALLY and forwards, so operator code
     never holds a raw key (the response is always KEY-FREE).
  2. AUTHORITATIVE money gate: EVERY call presents a capability and the safebox reserves -> settles the
     OPERATOR's control-plane budget (``billing.py``, the Takyon-user -> platform rail) keyed on the
     verified operator scope, BEFORE/AFTER resolving the key. There is NO ungated path — even the
     transitional internal-token path is metered. "Everything gated by usage/credits authoritatively on
     the safebox, no gating outside the safebox."

Scope after the creative-credit gate cutover: only the Anthropic (streaming) and Tavily proxy routes
live here. The ungated Gemini-image / OpenAI-image / FAL routes were DELETED — those creative providers
are reached ONLY through the AUTHORITATIVE creative-credit gate in ``safebox_app.py``.

The SESSION-scoped operator capability (audience ``operator.session``):

  The stock Anthropic SDK with a static key makes MANY streaming calls; a single-use-nonce capability
  cannot cover that. So the operator plane mints (``/v1/operator/session-token``) a SESSION-scoped
  capability: signed, operator+business-bound, with a per-CALL cost CEILING (``max_cost_microusd``) and a
  minutes-to-hours TTL, REUSABLE across calls (the proxy verifies it but does NOT claim a nonce). The
  proxy meters EACH call against the operator's control-plane budget keyed on the verified
  ``scope.takyon_user_id`` (the business owner = the operator). Each route also still accepts the
  matching per-action capability (``anthropic.messages`` / ``tavily.search``) so a token minted for that
  action works too.

Per-call money flow on every route:

  - Stream: reserve the ESTIMATED cost (from the request payload via ``billed_microusd_cost``) against
    the operator budget BEFORE forwarding -> stream the SSE verbatim (identity encoding + iter_bytes) ->
    SETTLE the ACTUAL cost parsed from the terminal ``message_delta`` / ``message_start`` usage events
    after the stream completes (release/refund the difference); release on any stream failure.
  - Non-stream: reserve estimate -> call -> settle actual from the response usage; release on failure.
  - Fail closed: if the operator is out of budget the call is REFUSED (clear error / SSE error event)
    BEFORE any provider key is resolved or any upstream call is made.

TRANSITIONAL internal-token path: these routes STILL accept the shared ``TAKYON_SAFEBOX_TOKEN`` so the
live CEO agent / worker do not break before they mint a session capability — BUT the internal-token path
is STILL money-gated: it reserves/settles against a PLATFORM operator budget (resolved from
``TAKYON_PLATFORM_OPERATOR_USER_ID`` / ``TAKYON_OPERATOR_USER_ID``). There is NO path that spends without
a safebox-side reserve/settle. This acceptance is the transitional path to REMOVE once all operator
clients mint session capabilities.

Hard invariants for every route here:

- Auth: a valid session-scoped operator capability (preferred) OR a per-action capability OR the
  transitional internal token. A wrong/absent credential fails closed with 401 before any work.
- Money: every call reserves -> (stream|call) -> settles on the operator rail. Out of budget -> 402 /
  SSE error BEFORE any upstream call. There is no ungated spend.
- Resolve the real key LOCALLY on the safebox; if it is empty -> 503 ``<provider>_unconfigured`` AFTER
  the reserve but BEFORE any upstream call (the reserve is released). Never proceed keyless.
- The real key NEVER appears in any response header or body. Upstream auth headers are never echoed
  back; upstream error bodies are surfaced sanitized (status + truncated body, no key).
- Connection failure to the upstream provider -> 502 (the reserve is released).
"""

from __future__ import annotations

import json as _json
import os as _os
import time as _time
from typing import Any, Iterator

import httpx
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

# Upstream provider hosts. Kept here (not in the business runtime) because only the safebox forwards.
_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"  # match ai_provider.ANTHROPIC_VERSION / call_anthropic

# Generous upstream timeout: provider calls (Anthropic / image gen) routinely exceed the 10s env-read
# timeout. Streaming uses no read timeout (the stream stays open for the life of the response).
_UPSTREAM_TIMEOUT_S = 180.0


def _as_json_object(body: Any) -> dict[str, Any]:
    """Coerce the parsed request body to a JSON object, or 400."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_json_body")
    return body


def _anthropic_key() -> str:
    """The SHARED Anthropic key, resolved LOCALLY on the safebox (never returned to the caller)."""
    from . import ai_provider

    return str(ai_provider.anthropic_key() or "").strip()


def _tavily_key() -> str:
    from . import ai_provider

    return str(ai_provider.tavily_key() or "").strip()


def _sanitize_upstream_error(status_code: int, body: str) -> HTTPException:
    """Map an upstream provider error to a clean HTTPException (truncated body, no key)."""
    return HTTPException(
        status_code=status_code,
        detail={"error": "provider_error", "upstream_status": int(status_code), "body": body[:500]},
    )


def _presented_credential(authorization: str | None, x_api_key: str | None) -> str:
    """The credential a caller presents. The Anthropic SDK sends it as ``x-api-key`` (so a caller can set
    ANTHROPIC_BASE_URL=<safebox> + ANTHROPIC_API_KEY=<safebox token / capability> and the stock SDK just
    works); other callers may use ``Authorization: Bearer``. x-api-key wins if present."""
    xk = str(x_api_key or "").strip()
    if xk:
        return xk
    auth = str(authorization or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth


# ── Operator-plane authorization (capability OR transitional internal token) ──────────────────────
class _ProxyAuth:
    """The outcome of authorizing an operator proxy call: the AUTHORITATIVE scope to meter against,
    plus a hard per-call ceiling and whether the ceiling must be enforced.

    ``scope`` is a ``CapabilityScope`` keyed on the operator's ``takyon_user_id`` (the rail key). For a
    capability it is the verified scope; for the transitional internal token it is a synthetic
    PLATFORM-operator scope. ``ceiling_microusd`` is the per-call cost ceiling; ``enforce_ceiling`` is
    True for a capability (the signed ``max_cost_microusd`` is a hard cap) and False for the internal
    token (fully trusted — metered but not ceiling-capped)."""

    __slots__ = ("scope", "ceiling_microusd", "enforce_ceiling", "via")

    def __init__(self, *, scope, ceiling_microusd: int, enforce_ceiling: bool, via: str):
        self.scope = scope
        self.ceiling_microusd = int(ceiling_microusd)
        self.enforce_ceiling = bool(enforce_ceiling)
        self.via = via


def _platform_operator_user_id() -> str:
    """The PLATFORM operator user id the TRANSITIONAL internal-token path meters against.

    Resolved safebox-side from ``TAKYON_PLATFORM_OPERATOR_USER_ID`` (preferred) or the legacy
    ``TAKYON_OPERATOR_USER_ID`` convenience. The internal token is trusted but its spend must still land
    on a real control-plane billing account, so this names that account. Returns "" when unset —
    the internal-token path then fails CLOSED (no ungated spend)."""
    return str(
        _os.environ.get("TAKYON_PLATFORM_OPERATOR_USER_ID")
        or _os.environ.get("TAKYON_OPERATOR_USER_ID")
        or ""
    ).strip()


def _authorize_operator_proxy(
    authorization: str | None,
    x_api_key: str | None,
    *,
    capability_audiences: "frozenset[str]",
) -> _ProxyAuth:
    """Authorize an operator proxy call and return the scope to meter against.

    Accepts, in order:
      (a) a valid signed, unexpired SESSION-scoped operator capability (audience ``operator.session``) OR
          the route's per-action capability (e.g. ``anthropic.messages`` / ``tavily.search``) — the
          verified scope is AUTHORITATIVE and its signed ceiling is enforced; the token is NOT
          single-use (no nonce claim), so it is reusable across the run's many calls;
      (b) the TRANSITIONAL shared internal token ``TAKYON_SAFEBOX_TOKEN`` — a synthetic PLATFORM-operator
          scope is metered against the platform operator budget (no ceiling, but reserve/settle still
          enforced). To be removed once all operator clients mint session capabilities.

    Fails closed 401 when neither is presented. The internal-token path additionally fails closed (503)
    when no platform operator identity is configured — there is no ungated path."""
    import hmac as _hmac

    from .safebox_app import (
        _OPERATOR_SESSION_AUDIENCE,
        _SAFEBOX_TOKEN_ENV,
        _allow_tokenless,
        _cap_signing_key,
    )
    from .safebox_capability import CapabilityError, CapabilityScope, verify_capability

    cred = _presented_credential(authorization, x_api_key)
    now = int(_time.time())

    # (a) Capability path — try the route's per-action audiences AND the operator-session audience. A
    # capability is verified (signature + audience + expiry) but NOT nonce-claimed: the operator session
    # token is reusable across the run's many calls, and the gate is the per-call money reserve below.
    signing_key = _cap_signing_key()
    accepted_audiences = frozenset(capability_audiences) | {_OPERATOR_SESSION_AUDIENCE}
    if signing_key and cred:
        for audience in accepted_audiences:
            try:
                scope, _nonce, _exp = verify_capability(
                    cred, signing_key=signing_key, expected_audience=audience, now=now
                )
            except CapabilityError:
                continue
            return _ProxyAuth(
                scope=scope,
                ceiling_microusd=int(scope.max_cost_microusd),
                enforce_ceiling=True,
                via=f"capability:{audience}",
            )

    # (b) TRANSITIONAL internal-token path — accepted but STILL money-gated against a platform operator.
    expected = str(_os.environ.get(_SAFEBOX_TOKEN_ENV) or "").strip()
    internal_ok = False
    if expected:
        if cred and _hmac.compare_digest(cred.encode(), expected.encode()):
            internal_ok = True
    elif _allow_tokenless():
        internal_ok = True
    if internal_ok:
        operator_user_id = _platform_operator_user_id()
        if not operator_user_id:
            # No ungated path: the internal token is accepted only when a platform operator budget exists
            # to meter against. Fail closed so the transitional path can never spend ungated.
            raise HTTPException(status_code=503, detail="platform_operator_unconfigured")
        scope = CapabilityScope(
            takyon_user_id=operator_user_id,
            # Platform-operator spend is NOT tied to a business; an empty slug -> billing.reserve gets
            # None (billing_entries.business_slug FKs businesses, and there is no "platform" business).
            business_slug="",
            app_user_id=None,
            action="operator.proxy",
            max_cost_microusd=0,
        )
        return _ProxyAuth(
            scope=scope, ceiling_microusd=0, enforce_ceiling=False, via="internal_token"
        )

    raise HTTPException(status_code=401, detail="unauthorized")


# ── Anthropic streaming usage parsing ─────────────────────────────────────────────────────────────
class _AnthropicStreamUsage:
    """Accumulates the realized Anthropic usage from the SSE event stream so the proxy can SETTLE the
    actual billed cost after the stream completes.

    ``message_start`` carries the input/cache token counts; ``message_delta`` carries the cumulative
    ``output_tokens`` (and the terminal stop). We track the last-seen values and, when the stream is
    done, price them through the SAME ``billed_microusd_cost`` the reserve estimate used."""

    def __init__(self, model: str):
        self.model = str(model or "")
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.saw_usage = False

    def _apply(self, usage: dict[str, Any]) -> None:
        if not isinstance(usage, dict):
            return
        if usage.get("input_tokens") is not None:
            self.input_tokens = int(usage.get("input_tokens") or 0)
        if usage.get("output_tokens") is not None:
            self.output_tokens = int(usage.get("output_tokens") or 0)
        if usage.get("cache_read_input_tokens") is not None:
            self.cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
        if usage.get("cache_creation_input_tokens") is not None:
            self.cache_write_tokens = int(usage.get("cache_creation_input_tokens") or 0)
        self.saw_usage = True

    def feed(self, chunk: bytes) -> None:
        """Parse any ``data: {...}`` JSON lines in the chunk and pull out usage. Best-effort: malformed
        or partial lines are ignored (the settle falls back to the estimate when no usage was seen)."""
        try:
            text = chunk.decode("utf-8", errors="ignore")
        except Exception:
            return
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                obj = _json.loads(payload)
            except (ValueError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            etype = obj.get("type")
            if etype == "message_start":
                msg = obj.get("message")
                if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
                    self._apply(msg["usage"])
            elif etype in {"message_delta", "message_stop"}:
                if isinstance(obj.get("usage"), dict):
                    self._apply(obj["usage"])

    def billed_microusd(self, *, fallback_microusd: int) -> int:
        """The realized billed cost in microUSD, or ``fallback_microusd`` (the reserve estimate) if no
        usage event was observed (so we never under-settle a streamed call to zero)."""
        if not self.saw_usage:
            return int(fallback_microusd)
        from . import ai_provider

        try:
            _realized, billed = ai_provider.billed_microusd_cost(
                self.model,
                int(self.input_tokens),
                int(self.output_tokens),
                cache_read_tokens=int(self.cache_read_tokens),
                cache_write_tokens=int(self.cache_write_tokens),
            )
        except Exception:
            return int(fallback_microusd)
        return int(billed)


# ── Per-provider estimate / actual pricing ────────────────────────────────────────────────────────
def _anthropic_estimate_microusd(payload: dict[str, Any]) -> int:
    """The SERVER-side reserve estimate for an Anthropic call: the billed cost of the canonical payload's
    estimated input tokens + the requested max_tokens (worst-case output). Fail-closed: an unpriced model
    raises so the reserve never runs on an unpriceable call."""
    from . import ai_provider

    _built, model, est_in = ai_provider.anthropic_payload(payload or {})
    max_tokens = int((_built or {}).get("max_tokens") or 0)
    _realized, billed = ai_provider.billed_microusd_cost(model, int(est_in), int(max_tokens))
    return int(billed)


def _anthropic_actual_microusd_from_response(payload: dict[str, Any], response_json: dict[str, Any]) -> int:
    """The realized billed cost for a NON-stream Anthropic response, from its ``usage`` block."""
    from . import ai_provider

    _built, model, est_in = ai_provider.anthropic_payload(payload or {})
    usage = (response_json or {}).get("usage") or {}
    in_tok = int(usage.get("input_tokens") or est_in)
    out_tok = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    cache_write = int(usage.get("cache_creation_input_tokens") or 0)
    _realized, billed = ai_provider.billed_microusd_cost(
        model, in_tok, out_tok, cache_read_tokens=cache_read, cache_write_tokens=cache_write
    )
    return int(billed)


def _tavily_price_microusd(operation: str, payload: dict[str, Any]) -> int:
    """The EXACT per-request Tavily price (estimate == actual for a per-request provider). Fail-closed:
    an unpriced operation raises so the reserve never runs on an unpriceable call."""
    from . import ai_provider

    units = max(1, int((payload or {}).get("units") or 1))
    return int(ai_provider.tavily_request_microusd(str(operation), units=units))


# ── Money-gate helpers (operator rail) ────────────────────────────────────────────────────────────
def _reserve_or_refuse(auth: _ProxyAuth, estimate_microusd: int):
    """Ceiling-check (for a capability) then RESERVE the operator budget. Returns the reservation handle.

    Raises HTTPException(402) on an over-ceiling estimate or an exhausted operator budget — BEFORE any
    provider key is resolved or any upstream call is made. Raises HTTPException(503) on a fail-closed
    pricing/identity problem."""
    from .safebox_app import OperatorBudgetExceeded, _OperatorBudgetAdapter

    est = int(estimate_microusd)
    if auth.enforce_ceiling and est > int(auth.ceiling_microusd):
        raise HTTPException(status_code=402, detail="estimate_exceeds_ceiling")
    ledger = _OperatorBudgetAdapter()
    try:
        reservation = ledger.reserve(auth.scope, est)
    except OperatorBudgetExceeded as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "operator_budget_exceeded",
                "estimate_cents": exc.estimate_cents,
                "allowance_available_cents": exc.allowance_available_cents,
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001 — BrokerLedgerError (missing identity / no account) is fail-closed
        message = str(exc)
        if message.endswith("_missing") or message.endswith("_no_billing_account"):
            raise HTTPException(status_code=503, detail=message) from exc
        raise HTTPException(status_code=502, detail="operator_budget_error") from exc
    return ledger, reservation


def _settle(ledger, reservation, actual_microusd: int) -> None:
    try:
        ledger.settle(reservation, int(actual_microusd))
    except Exception:  # noqa: BLE001 — settle must never surface to the caller after a successful call
        pass


def _release(ledger, reservation) -> None:
    try:
        ledger.release(reservation)
    except Exception:  # noqa: BLE001 — release is best-effort cleanup on the failure path
        pass


def register_provider_proxy_routes(app: FastAPI) -> None:
    """Register the operator/platform provider-proxy routes DIRECTLY on the safebox app (flat APIRoute
    entries, matching every other route in ``build_safebox_app()``)."""
    from .safebox_app import _ANTHROPIC_AUDIENCE, _TAVILY_AUDIENCE

    router = app

    # ── Anthropic Messages (streaming-capable, money-gated passthrough) ───────────────────────────
    def _anthropic_passthrough(payload: dict[str, Any], auth: _ProxyAuth):
        """Reserve -> resolve key -> (stream|call) -> settle. The key is injected ONLY into the outbound
        request headers; it never appears in the response."""
        # 1. RESERVE the operator budget on the worst-case estimate BEFORE any key resolution / upstream
        #    call. Out of budget / unpriced model -> refused here (402 / 503).
        try:
            estimate = _anthropic_estimate_microusd(payload)
        except Exception as exc:  # noqa: BLE001 — AnthropicPricingUnavailable etc. -> fail-closed 503
            raise HTTPException(status_code=503, detail="anthropic_pricing_unavailable") from exc
        ledger, reservation = _reserve_or_refuse(auth, estimate)

        # 2. Resolve the key LOCALLY (release the hold and 503 if unconfigured — never proceed keyless).
        key = _anthropic_key()
        if not key:
            _release(ledger, reservation)
            raise HTTPException(status_code=503, detail="anthropic_unconfigured")

        headers = {
            "x-api-key": key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
            # Force an UNCOMPRESSED upstream response so the SSE bytes pass through verbatim and the SDK
            # can decode them (httpx auto-adds accept-encoding: gzip otherwise).
            "accept-encoding": "identity",
        }
        wants_stream = bool(payload.get("stream") is True)

        if wants_stream:
            from . import ai_provider

            try:
                _built, model, _est_in = ai_provider.anthropic_payload(payload or {})
            except Exception:  # noqa: BLE001 — model resolution best-effort; usage parse falls back to estimate
                model = str((payload or {}).get("model") or "")
            usage = _AnthropicStreamUsage(model)

            def _sse_bytes() -> Iterator[bytes]:
                settled = {"done": False}

                def _finish_settle() -> None:
                    if settled["done"]:
                        return
                    settled["done"] = True
                    actual = usage.billed_microusd(fallback_microusd=estimate)
                    _settle(ledger, reservation, actual)

                client = httpx.Client(timeout=httpx.Timeout(_UPSTREAM_TIMEOUT_S, read=None))
                try:
                    with client.stream(
                        "POST", _ANTHROPIC_MESSAGES_URL, headers=headers, json=payload
                    ) as upstream:
                        if upstream.status_code >= 400:
                            # Upstream rejected: no provider spend realized -> RELEASE the hold and emit a
                            # sanitized error event (no key).
                            _ = upstream.read()
                            _release(ledger, reservation)
                            settled["done"] = True
                            yield (
                                f"event: error\ndata: "
                                f'{{"upstream_status": {int(upstream.status_code)}, '
                                f'"error": "provider_error"}}\n\n'
                            ).encode("utf-8")
                            return
                        for chunk in upstream.iter_bytes():
                            if chunk:
                                usage.feed(chunk)
                                yield chunk
                    # Stream completed cleanly: settle the ACTUAL parsed usage (release/refund the diff).
                    _finish_settle()
                except Exception:  # noqa: BLE001 — a mid-stream failure releases the hold (no settle).
                    if not settled["done"]:
                        settled["done"] = True
                        _release(ledger, reservation)
                    raise
                finally:
                    client.close()
                    # Defensive: if we somehow exited without settling or releasing, settle the estimate
                    # so a hold is never orphaned.
                    if not settled["done"]:
                        _finish_settle()

            return StreamingResponse(_sse_bytes(), media_type="text/event-stream")

        # Non-streaming: call -> settle actual from the response usage. Release on transport failure.
        try:
            with httpx.Client(timeout=_UPSTREAM_TIMEOUT_S) as client:
                resp = client.post(_ANTHROPIC_MESSAGES_URL, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            _release(ledger, reservation)
            raise HTTPException(status_code=502, detail="provider_unreachable") from exc
        text = resp.text
        if resp.status_code >= 400:
            # Upstream rejected: no realized spend -> release the hold and surface a sanitized error.
            _release(ledger, reservation)
            raise _sanitize_upstream_error(resp.status_code, text)
        try:
            data = _json.loads(text) if text.strip() else {}
        except (ValueError, TypeError):
            data = {}
        try:
            actual = _anthropic_actual_microusd_from_response(payload, data if isinstance(data, dict) else {})
        except Exception:  # noqa: BLE001 — settle the reserved estimate if pricing the response fails
            actual = estimate
        _settle(ledger, reservation, actual)
        return JSONResponse(content=data, status_code=resp.status_code)

    def _anthropic_messages(body: Any, authorization: str | None, x_api_key: str | None):
        auth = _authorize_operator_proxy(
            authorization, x_api_key, capability_audiences=frozenset({_ANTHROPIC_AUDIENCE})
        )
        payload = _as_json_object(body)
        return _anthropic_passthrough(payload, auth)

    @router.post("/v1/proxy/anthropic/messages")
    def proxy_anthropic_messages(
        body: Any = Body(default=None),
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ):
        return _anthropic_messages(body, authorization, x_api_key)

    @router.post("/v1/messages")
    def proxy_anthropic_messages_sdk(
        body: Any = Body(default=None),
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ):
        # ALSO mounted at the stock Anthropic SDK path so a caller can set ANTHROPIC_BASE_URL to the
        # safebox root and have the SDK work unmodified.
        return _anthropic_messages(body, authorization, x_api_key)

    # ── Tavily search / extract (money-gated passthrough) ─────────────────────────────────────────
    @router.post("/v1/proxy/tavily/{operation}")
    def proxy_tavily(
        operation: str,
        body: Any = Body(default=None),
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ) -> dict[str, Any]:
        auth = _authorize_operator_proxy(
            authorization, x_api_key, capability_audiences=frozenset({_TAVILY_AUDIENCE})
        )
        op = str(operation or "").strip().lower()
        if op not in {"search", "extract"}:
            raise HTTPException(status_code=400, detail="unsupported_tavily_operation")
        from . import ai_provider

        payload = _as_json_object(body)
        # RESERVE the operator budget on the exact per-request price BEFORE any key resolution / call.
        try:
            price = _tavily_price_microusd(op, payload)
        except Exception as exc:  # noqa: BLE001 — TavilyPricingUnavailable -> fail-closed 503
            raise HTTPException(status_code=503, detail="tavily_pricing_unavailable") from exc
        ledger, reservation = _reserve_or_refuse(auth, price)

        key = _tavily_key()
        if not key:
            _release(ledger, reservation)
            raise HTTPException(status_code=503, detail="tavily_unconfigured")
        try:
            result = ai_provider.call_tavily(op, payload, key)
        except RuntimeError as exc:
            # call_tavily raises RuntimeError on HTTP error — no realized spend -> release the hold.
            _release(ledger, reservation)
            raise HTTPException(
                status_code=502, detail={"error": "provider_error", "body": str(exc)[:500]}
            ) from exc
        except Exception:  # noqa: BLE001 — any other failure releases the hold
            _release(ledger, reservation)
            raise
        # Per-request provider: actual == the reserved price.
        _settle(ledger, reservation, price)
        return result

    # NOTE: the ungated /v1/proxy/gemini/image, /v1/proxy/openai/images, and /v1/proxy/fal/{path}
    # routes were DELETED in the creative-credit safebox-gate cutover. Those provider calls now go
    # through the AUTHORITATIVE creative-credit gate on the safebox (see safebox_app.py). There is no
    # ungated provider proxy for these creative providers any more.
