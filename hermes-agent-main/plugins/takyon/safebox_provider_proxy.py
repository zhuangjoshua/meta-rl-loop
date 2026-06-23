"""Operator/platform PROVIDER PROXY routes on the safebox service app.

This is the operator/platform counterpart to the business-scoped capability broker at
``/v1/providers/*`` (see ``safebox_app.py``). Where the broker is the metered, capability-gated path
for product (sub-user) spend, THIS proxy is the TRUSTED operator/platform plane path: internal-token
only, NO capability and NO per-call metering (these calls are platform-billed). Its single purpose is
to let operator/platform/worker code call paid providers WITHOUT ever holding a raw key — the safebox
resolves the real key LOCALLY (the same resolvers the broker uses), forwards the request, and returns
a KEY-FREE response. It is the unblocker that lets every runtime plane go keyless.

Hard invariants for every route here:

- Auth: ``_require_internal_token`` (the shared ``TAKYON_SAFEBOX_TOKEN``). A wrong/absent token fails
  closed with 401 before any work.
- Resolve the real key LOCALLY on the safebox; if it is empty -> 503 ``<provider>_unconfigured`` BEFORE
  any upstream call. Never proceed keyless.
- The real key NEVER appears in any response header or body. Upstream auth headers are never echoed
  back, and upstream error bodies are surfaced sanitized (status + truncated body, no key).
- Connection failure to the upstream provider -> 502.

The Anthropic route additionally supports SSE streaming so the stock Anthropic SDK can point
``ANTHROPIC_BASE_URL`` at the safebox root and stream verbatim.
"""

from __future__ import annotations

from typing import Any, Iterator

import httpx
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

# Upstream provider hosts. Kept here (not in the business runtime) because only the safebox forwards.
_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"  # match ai_provider.ANTHROPIC_VERSION / call_anthropic
_OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
_FAL_BASE_URL = "https://fal.run"

# Generous upstream timeout: provider calls (Anthropic / image gen) routinely exceed the 10s env-read
# timeout. Streaming uses no read timeout (the stream stays open for the life of the response).
_UPSTREAM_TIMEOUT_S = 180.0


def _as_json_object(body: Any) -> dict[str, Any]:
    """Coerce the parsed request body to a JSON object, or 400. FastAPI parses the request JSON into
    ``body`` (a dict for a JSON object) for sync routes; we forward that dict to the provider verbatim
    after stream-detection / light validation."""
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


def _gemini_image_key() -> str:
    from . import creative_gateway

    return str(creative_gateway._resolve_gemini_image_key() or "").strip()


def _openai_key() -> str:
    """The SHARED OpenAI key, resolved LOCALLY on the safebox via the canonical alias (core
    ``_API_ENV_ALIASES['openai']`` = ``OPENAI_API_KEY``). Mirrors the other resolvers: safebox-side
    only, returns "" when unconfigured so the route can fail closed with a 503."""
    from . import safebox

    try:
        return str(safebox.first_env_backed_value("OPENAI_API_KEY") or "").strip()
    except Exception:
        return ""


def _fal_key() -> str:
    """The SHARED FAL key, resolved LOCALLY on the safebox via the canonical aliases (core
    ``_API_ENV_ALIASES['fal']`` = ``FAL_KEY`` / ``FAL_API_KEY``)."""
    from . import safebox

    try:
        return str(safebox.first_env_backed_value("FAL_KEY", "FAL_API_KEY") or "").strip()
    except Exception:
        return ""


def _sanitize_upstream_error(status_code: int, body: str) -> HTTPException:
    """Map an upstream provider error to a clean HTTPException. The body is truncated and never carries
    the request auth header (we only forward the upstream RESPONSE body, which has no key), so this is
    safe to surface. Connection failures are handled at the call site as 502."""
    return HTTPException(
        status_code=status_code,
        detail={"error": "provider_error", "upstream_status": int(status_code), "body": body[:500]},
    )


def register_provider_proxy_routes(app: FastAPI) -> None:
    """Register the operator/platform provider-proxy routes DIRECTLY on the safebox app.

    Routes are attached to ``app`` rather than mounted via ``include_router`` so they appear as plain
    ``APIRoute`` entries in ``app.routes`` (no nested ``_IncludedRouter`` wrapper), matching every other
    route in ``build_safebox_app()`` and keeping ``app.routes`` flat for callers that introspect it."""
    from .safebox_app import _require_internal_token

    router = app

    # ── Anthropic Messages (streaming-capable passthrough) ───────────────────────────────────────
    def _anthropic_passthrough(payload: dict[str, Any]):
        """Forward a Messages request to Anthropic with the LOCALLY-resolved key. Returns either a
        non-streaming JSON Response payload (dict + status) or a StreamingResponse for ``stream:true``.
        The key is injected ONLY into the outbound request headers; it never appears in the response."""
        key = _anthropic_key()
        if not key:
            # Fail closed BEFORE any upstream call.
            raise HTTPException(status_code=503, detail="anthropic_unconfigured")

        headers = {
            "x-api-key": key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        wants_stream = bool(payload.get("stream") is True)

        if wants_stream:
            # Proxy the upstream SSE bytes through VERBATIM. The app is sync FastAPI, so we use a sync
            # generator backed by httpx.Client.stream and keep the client open for the life of the
            # stream (closed when the generator is exhausted / the response is torn down).
            def _sse_bytes() -> Iterator[bytes]:
                client = httpx.Client(timeout=httpx.Timeout(_UPSTREAM_TIMEOUT_S, read=None))
                try:
                    with client.stream(
                        "POST", _ANTHROPIC_MESSAGES_URL, headers=headers, json=payload
                    ) as upstream:
                        if upstream.status_code >= 400:
                            # Surface the upstream error inside the stream body (sanitized, no key).
                            body = upstream.read().decode("utf-8", errors="replace")
                            yield (
                                f"event: error\ndata: "
                                f'{{"upstream_status": {int(upstream.status_code)}, '
                                f'"error": "provider_error"}}\n\n'
                            ).encode("utf-8")
                            return
                        for chunk in upstream.iter_raw():
                            if chunk:
                                yield chunk
                finally:
                    client.close()

            return StreamingResponse(_sse_bytes(), media_type="text/event-stream")

        # Non-streaming: return the upstream JSON with the upstream status code. Never echo upstream
        # auth/response headers — only the parsed JSON body, which carries no key.
        try:
            with httpx.Client(timeout=_UPSTREAM_TIMEOUT_S) as client:
                resp = client.post(_ANTHROPIC_MESSAGES_URL, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="provider_unreachable") from exc
        text = resp.text
        if resp.status_code >= 400:
            raise _sanitize_upstream_error(resp.status_code, text)
        import json as _json

        try:
            data = _json.loads(text) if text.strip() else {}
        except (ValueError, TypeError):
            data = {}
        return JSONResponse(content=data, status_code=resp.status_code)

    def _anthropic_messages(body: Any, authorization: str | None):
        _require_internal_token(authorization)
        payload = _as_json_object(body)
        return _anthropic_passthrough(payload)

    @router.post("/v1/proxy/anthropic/messages")
    def proxy_anthropic_messages(
        body: Any = Body(default=None), authorization: str | None = Header(default=None)
    ):
        return _anthropic_messages(body, authorization)

    @router.post("/v1/messages")
    def proxy_anthropic_messages_sdk(
        body: Any = Body(default=None), authorization: str | None = Header(default=None)
    ):
        # ALSO mounted at the stock Anthropic SDK path so a caller can set ANTHROPIC_BASE_URL to the
        # safebox root and have the SDK work unmodified.
        return _anthropic_messages(body, authorization)

    # ── Tavily search / extract passthrough ──────────────────────────────────────────────────────
    @router.post("/v1/proxy/tavily/{operation}")
    def proxy_tavily(
        operation: str,
        body: Any = Body(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        op = str(operation or "").strip().lower()
        if op not in {"search", "extract"}:
            raise HTTPException(status_code=400, detail="unsupported_tavily_operation")
        from . import ai_provider

        key = _tavily_key()
        if not key:
            raise HTTPException(status_code=503, detail="tavily_unconfigured")
        payload = _as_json_object(body)
        try:
            return ai_provider.call_tavily(op, payload, key)
        except RuntimeError as exc:
            # call_tavily raises RuntimeError("Tavily API returned <code>: <body>") on HTTP error. The
            # body is the upstream RESPONSE (no key). Surface as 502 (sanitized).
            raise HTTPException(
                status_code=502, detail={"error": "provider_error", "body": str(exc)[:500]}
            ) from exc

    # ── Gemini image passthrough ─────────────────────────────────────────────────────────────────
    @router.post("/v1/proxy/gemini/image")
    def proxy_gemini_image(
        body: Any = Body(default=None), authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        import base64 as _b64

        from . import creative_gateway

        key = _gemini_image_key()
        if not key:
            raise HTTPException(status_code=503, detail="gemini_unconfigured")
        payload = _as_json_object(body)
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="missing_prompt")
        try:
            png_bytes = creative_gateway._gemini_generate_logo_png(api_key=key, prompt=prompt)
        except Exception as exc:  # provider/library failure — never leak the upstream/key
            raise HTTPException(
                status_code=502, detail={"error": "provider_error", "detail": str(exc)[:300]}
            ) from exc
        return {"image_base64": _b64.b64encode(png_bytes).decode("ascii"), "format": "png"}

    # ── OpenAI image passthrough ─────────────────────────────────────────────────────────────────
    @router.post("/v1/proxy/openai/images")
    def proxy_openai_images(
        body: Any = Body(default=None), authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        key = _openai_key()
        if not key:
            raise HTTPException(status_code=503, detail="openai_unconfigured")
        payload = _as_json_object(body)
        headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
        try:
            with httpx.Client(timeout=_UPSTREAM_TIMEOUT_S) as client:
                resp = client.post(_OPENAI_IMAGES_URL, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="provider_unreachable") from exc
        text = resp.text
        if resp.status_code >= 400:
            raise _sanitize_upstream_error(resp.status_code, text)
        import json as _json

        try:
            return _json.loads(text) if text.strip() else {}
        except (ValueError, TypeError):
            return {}

    # ── FAL passthrough ──────────────────────────────────────────────────────────────────────────
    @router.post("/v1/proxy/fal/{path:path}")
    def proxy_fal(
        path: str,
        body: Any = Body(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        fal_path = str(path or "").strip().strip("/")
        if not fal_path:
            raise HTTPException(status_code=400, detail="missing_fal_path")
        key = _fal_key()
        if not key:
            raise HTTPException(status_code=503, detail="fal_unconfigured")
        payload = _as_json_object(body)
        headers = {"Authorization": f"Key {key}", "content-type": "application/json"}
        url = f"{_FAL_BASE_URL}/{fal_path}"
        try:
            with httpx.Client(timeout=_UPSTREAM_TIMEOUT_S) as client:
                resp = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="provider_unreachable") from exc
        text = resp.text
        if resp.status_code >= 400:
            raise _sanitize_upstream_error(resp.status_code, text)
        import json as _json

        try:
            return _json.loads(text) if text.strip() else {}
        except (ValueError, TypeError):
            return {}
