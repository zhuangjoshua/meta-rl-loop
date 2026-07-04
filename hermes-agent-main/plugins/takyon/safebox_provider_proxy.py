"""Operator/platform PROVIDER PROXY routes on the safebox service app — AUTHORITATIVELY MONEY-GATED.

This is the operator/platform counterpart to the business-scoped capability broker at ``/v1/providers/*``
(see ``safebox_app.py``). Where that broker meters PRODUCT (sub-user) spend, THIS proxy meters the
operator/platform plane — the CEO agent + coding worker + platform ``web_tools`` calling Anthropic /
Tavily with the stock SDK against a STATIC key. Its purpose is twofold:

  1. Keyless egress: the safebox resolves the real provider key LOCALLY and forwards, so operator code
     never holds a raw key (the response is always KEY-FREE).
  2. AUTHORITATIVE money gate: EVERY call presents a capability and the safebox reserves -> settles the
     OPERATOR's control-plane budget (``billing.py``, the Takyon-user -> platform rail) keyed on the
     verified operator scope, BEFORE/AFTER resolving the key. There is NO ungated path, and the shared
     Safebox transport token is never accepted as spend authority. "Everything gated by usage/credits
     authoritatively on the safebox, no gating outside the safebox."

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

Hard invariants for every route here:

- Auth: a valid session-scoped operator capability (preferred) OR a per-action capability. A wrong,
  absent, or bare shared-token credential fails closed with 401 before any work.
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


# ── Operator-plane authorization (capability only) ────────────────────────────────────────────────
class _ProxyAuth:
    """The outcome of authorizing an operator proxy call: the AUTHORITATIVE scope to meter against,
    plus a hard per-call ceiling and whether the ceiling must be enforced.

    ``scope`` is a verified ``CapabilityScope`` keyed on the operator's ``takyon_user_id`` (the rail
    key). ``ceiling_microusd`` is the signed per-call cost ceiling, enforced for every capability."""

    __slots__ = ("scope", "ceiling_microusd", "enforce_ceiling", "via")

    def __init__(self, *, scope, ceiling_microusd: int, enforce_ceiling: bool, via: str):
        self.scope = scope
        self.ceiling_microusd = int(ceiling_microusd)
        self.enforce_ceiling = bool(enforce_ceiling)
        self.via = via


def _authorize_operator_proxy(
    authorization: str | None,
    x_api_key: str | None,
    *,
    capability_audiences: "frozenset[str]",
) -> _ProxyAuth:
    """Authorize an operator proxy call and return the scope to meter against.

    Authority is a CAPABILITY ONLY (authority principle / G2): a valid signed, unexpired SESSION-scoped
    operator capability (audience ``operator.session``) OR the route's per-action capability (e.g.
    ``anthropic.messages`` / ``tavily.search``). The verified scope is AUTHORITATIVE and its signed
    per-call ceiling is enforced; the token is NOT single-use (no nonce claim), so it is reusable across
    the run's many calls. The shared internal ``TAKYON_SAFEBOX_TOKEN`` is TRANSPORT reachability, never
    spend authority — every runtime plane holds it, so accepting it would let anyone who compromises a
    plane spend uncapped against the platform operator budget. Fails closed 401 when no valid capability
    is presented."""
    from .safebox_app import (
        _OPERATOR_SESSION_AUDIENCE,
        _cap_signing_key,
    )
    from .safebox_capability import CapabilityError, verify_capability

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

    # (b) The shared internal token is NOT spend authority (authority principle / G2). It is held by
    # every runtime plane, so accepting it for spend gave anyone who compromised a plane an uncapped
    # per-call channel against the platform operator budget. Operator clients (CEO loop, coding worker)
    # present a minted, ceiling-bound operator.session capability via branch (a); a bare token — or
    # anything unsigned — is refused. No ungated, no token-authorized spend path remains.
    raise HTTPException(status_code=401, detail="operator_capability_required")


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
        # Persistent line buffer: SSE lines can split across raw byte chunks, so we accumulate the
        # trailing incomplete fragment here and only process complete (newline-terminated) lines.
        self._line_buffer = ""

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
        lines are ignored (the settle falls back to the estimate when no usage was seen).

        Maintains a persistent line buffer so a line split across raw byte chunk boundaries is
        reassembled and parsed once, and only complete (newline-terminated) lines are processed."""
        try:
            text = chunk.decode("utf-8", errors="ignore")
        except Exception:
            return
        self._line_buffer += text
        # Split on newline; the last element is the (possibly empty) trailing incomplete fragment,
        # which we keep buffered for the next feed().
        lines = self._line_buffer.split("\n")
        self._line_buffer = lines.pop()
        for raw in lines:
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            # Only the message_start/message_delta/message_stop events carry usage. Cheaply skip
            # everything else (content deltas etc.) before paying for a json.loads on the hot path.
            if not (
                "message_start" in payload
                or "message_delta" in payload
                or "message_stop" in payload
            ):
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
                status_code=502, detail={"error": "provider_error"}
            ) from exc
        except Exception:  # noqa: BLE001 — any other failure releases the hold
            _release(ledger, reservation)
            raise
        # Per-request provider: actual == the reserved price.
        _settle(ledger, reservation, price)
        return result

    # ── Generic credentialed egress (delta 6) — the "any integration" rail ────────────────────────
    @router.post("/v1/egress")
    def proxy_egress(
        body: Any = Body(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Keyless, metered, SSRF-guarded egress. The subuser rail forwards {business, session_token,
        connection_slug, method, path, query, headers, body, estimate_microusd}. The credential is
        resolved + attached ONLY inside egress_gateway on the safebox; the response is key-free.

        Auth: _require_internal_token (transport reachability) + a signed connection.egress capability
        as the SOLE spend authority (never the bare token — authority principle / G2). The scope is
        ALWAYS session-derived (app_user_id present), so egress meters per-customer and can never
        select an uncapped service principal (must-fix #10)."""
        from .safebox_app import (
            _EGRESS_AUDIENCE, _PgNonceStore, _UsageLedgerAdapter, _cap_signing_key,
            _mint_capability_token, _require_internal_token, _safebox_db_conn, _CAP_TTL_SECONDS,
        )
        from . import ai_provider, app_usage, egress_gateway, safebox_broker
        from .safebox_capability import CapabilityError

        _require_internal_token(authorization)  # must-fix #6

        b = _as_json_object(body)
        business = str(b.get("business") or "").strip()
        session_token = str(b.get("session_token") or "").strip()
        connection_slug = str(b.get("connection_slug") or "").strip()
        method = str(b.get("method") or "GET")
        path = str(b.get("path") or "/")
        query = b.get("query") if isinstance(b.get("query"), dict) else None
        headers = b.get("headers") if isinstance(b.get("headers"), dict) else None
        req_body = b.get("body")
        if not business or not session_token or not connection_slug:
            raise HTTPException(status_code=400, detail="missing_egress_identity")

        signing_key = _cap_signing_key()
        if not signing_key:
            raise HTTPException(status_code=503, detail="capability_signing_unconfigured")

        # Estimate BEFORE reserve (fail-closed pricing). Reserve a MODEST response estimate (not the
        # 1 MiB hard cap) so egress is affordable on normal plans; settle re-prices on the ACTUAL
        # response bytes, and the hard response cap (response_too_large) bounds the true maximum. A
        # response between the estimate and the cap settles above the reserve (money-truth, exactly
        # as the AI rails allow) — no money is lost, the next reserve sees the higher committed.
        _EGRESS_RESERVE_RESPONSE_BYTES = 64 * 1024
        try:
            req_bytes = len(req_body.encode("utf-8")) if isinstance(req_body, str) else (
                len(_json.dumps(req_body).encode("utf-8")) if req_body is not None else 0)
            estimate = ai_provider.egress_request_microusd(
                request_bytes=req_bytes, response_bytes=_EGRESS_RESERVE_RESPONSE_BYTES
            )
        except ai_provider.EgressPricingUnavailable as exc:
            raise HTTPException(status_code=503, detail="egress_pricing_unavailable") from exc

        # ALWAYS session-derived scope (must-fix #10): no operator_user_id path on the subuser rail.
        now = int(_time.time())
        token = _mint_capability_token(
            business=business, action=_EGRESS_AUDIENCE, max_cost_microusd=estimate,
            session_token=session_token, operator_user_id=None, audience=_EGRESS_AUDIENCE,
            ttl_seconds=_CAP_TTL_SECONDS, now=now,
        )

        # Resolve the connection + credential inside the safebox; both key_resolver and provider_caller
        # close over the row so it is resolved once against the SIGNED scope.business_slug.
        state: dict[str, Any] = {}

        def key_resolver(scope):
            # Transaction-pooler-safe (:6543 probe gotcha): the RLS-bypass GUC that
            # configure_takyon_pg_session set at session level is not reliably carried to the
            # backend running a later autocommit statement, so provider_connections (RLS on) could
            # nondeterministically return no row. Pin the read: one transaction, SET LOCAL the
            # bypass on THIS backend, then resolve (connection + sealed secret in one statement).
            with _safebox_db_conn() as conn:
                with conn.transaction():
                    conn.execute("select set_config('takyon.rls_bypass', '1', true)")
                    connection = egress_gateway.resolve_active_connection(
                        conn, scope.business_slug, connection_slug
                    )
            secret = egress_gateway._unseal_secret(
                connection.secret_ciphertext, connection.secret_nonce
            )
            state["connection"] = connection
            state["fingerprint"] = str(connection.secret_fingerprint or "")
            return secret

        def provider_caller(scope, secret):
            connection = state["connection"]
            result = egress_gateway.call_egress(
                connection, method=method, path=path, query=query, headers=headers,
                body=req_body, secret=secret, fingerprint=state.get("fingerprint", ""),
            )
            # SETTLE on ACTUAL bytes, not the 1MB-headroom reserve — the reserve worst-cases the
            # response cap (so the customer is never under-billed and a huge response can't exceed
            # the hold), but the settle prices the REAL request+response size and the broker refunds
            # the difference. Same reserve-worst-case / settle-actual shape as the AI rails; without
            # this a 56-byte response would be charged as if it were 1 MiB.
            resp_bytes = len((result.get("body") or "").encode("utf-8"))
            actual = ai_provider.egress_request_microusd(
                request_bytes=req_bytes, response_bytes=resp_bytes
            )
            # Settle the ACTUAL (money-truth) — may exceed the modest reserve when the response is
            # large; the ledger allows settle > reserve and the response body cap bounds the max.
            return result, actual

        try:
            return safebox_broker.handle_provider_request(
                token=token, signing_key=signing_key, audience=_EGRESS_AUDIENCE, now=now,
                nonce_store=_PgNonceStore(), ledger=_UsageLedgerAdapter(provider="egress"),
                key_resolver=key_resolver, provider_caller=provider_caller,
                estimate_microusd=estimate,
            )
        except egress_gateway.EgressError as exc:
            raise HTTPException(status_code=exc.status, detail={"error": exc.code, "detail": exc.detail}) from exc
        except CapabilityError as exc:
            raise HTTPException(status_code=401, detail=f"capability_invalid: {exc}") from exc
        except safebox_broker.BrokerError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        except (app_usage.AppBudgetInactive, app_usage.AppBudgetExceeded, app_usage.AppUserBudgetExceeded) as exc:
            raise HTTPException(status_code=402, detail={"error": type(exc).__name__, "detail": str(exc)}) from exc
        except app_usage.AppUserNotFound as exc:
            raise HTTPException(status_code=400, detail="unknown_app_user") from exc

    # NOTE: the ungated /v1/proxy/gemini/image, /v1/proxy/openai/images, and /v1/proxy/fal/{path}
    # routes were DELETED in the creative-credit safebox-gate cutover. Those provider calls now go
    # through the AUTHORITATIVE creative-credit gate on the safebox (see safebox_app.py). There is no
    # ungated provider proxy for these creative providers any more.
