"""Anthropic provider leaf — the one place that builds a Messages request, prices it in microUSD,
calls the SHARED platform provider key, and parses the response.

Lifted out of the old SQLite app runtime so the Internal AI Gateway (``ai_gateway.py``) owns ONE
implementation of "what does a token cost / how do we call Anthropic", never two copies that can
silently drift. The retired standalone app API is gone; this leaf is the survivor.

Pure and side-effect-free at import. The only outbound effect is ``call_anthropic``, which performs
the real HTTPS POST using the provider key the CALLER passes in — the shared key is resolved by
``anthropic_key()`` server-side, is never an argument the product app supplies, and is never
returned to a caller. Cost rates are conservative per-model estimates, overridable by env for exact
pricing.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from decimal import ROUND_CEILING, Decimal

from agent.usage_pricing import (
    CanonicalUsage,
    billed_cost,
    estimate_usage_cost,
    get_pricing_entry,
    usage_markup_bps,
)

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
_ONE_MILLION = Decimal("1000000")


class AnthropicPricingUnavailable(ValueError):
    """Raised when the requested Anthropic model has no exact known pricing."""


class OpenAIPricingUnavailable(ValueError):
    """Raised when the requested OpenAI model has no exact known pricing."""


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def _safebox_env_value(*names: str) -> str:
    resolved_names = [str(name or "").strip() for name in names if str(name or "").strip()]
    if not resolved_names:
        return ""
    try:
        from . import safebox

        value = str(safebox.first_env_backed_value(*resolved_names) or "").strip()
    except Exception:
        value = ""
    if value:
        return value
    return next((_env(name) for name in resolved_names if _env(name)), "")


def anthropic_env() -> dict[str, str]:
    """Anthropic auth env for runtime callers.

    Resolve from Safebox first so operator/subuser runtimes do not require local raw provider
    secrets. ``CLAUDE_CODE_OAUTH_TOKEN`` is normalized onto ``ANTHROPIC_TOKEN`` because the Claude
    worker lane consumes the latter.
    """
    resolved: dict[str, str] = {}
    api_key = _safebox_env_value("ANTHROPIC_API_KEY")
    token = _safebox_env_value("ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
    if api_key:
        resolved["ANTHROPIC_API_KEY"] = api_key
    if token:
        resolved["ANTHROPIC_TOKEN"] = token
    return resolved


def anthropic_key() -> str:
    """The SHARED platform provider key, resolved server-side.

    Safebox is authoritative when configured; local env remains a compatibility fallback. Returns
    ``""`` when none is configured — callers MUST treat that as blocked (invariant #8), never as
    permission to proceed keyless.
    """
    resolved = anthropic_env()
    return resolved.get("ANTHROPIC_API_KEY") or resolved.get("ANTHROPIC_TOKEN", "")


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(value))
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def anthropic_model(body: dict) -> str:
    return str(
        body.get("model")
        or _env("TAKYON_APP_ANTHROPIC_MODEL")
        or _env("ANTHROPIC_MODEL")
        or "claude-sonnet-4-6"
    ).strip()


def _canonical_model_name(model: object) -> str:
    name = str(model or "").strip()
    if "/" in name:
        _provider, _sep, tail = name.partition("/")
        if tail:
            name = tail
    return name


def _is_deepseek_model(model: object) -> bool:
    return _canonical_model_name(model).lower().startswith("deepseek")


def _pricing_provider(model: str) -> str:
    return "deepseek" if _is_deepseek_model(model) else "anthropic"


def _anthropic_pricing_source_label(model: str) -> str:
    entry = get_pricing_entry(model, provider=_pricing_provider(model))
    if entry is None:
        raise AnthropicPricingUnavailable(
            f"no exact Anthropic pricing is configured for model {model!r}"
        )
    parts = [entry.source]
    if entry.pricing_version:
        parts.append(entry.pricing_version)
    return ":".join(part for part in parts if part)


def anthropic_rates_microusd_per_token(model: str) -> tuple[Decimal, Decimal, str]:
    input_override = _env("TAKYON_APP_ANTHROPIC_INPUT_MICROUSD_PER_TOKEN")
    output_override = _env("TAKYON_APP_ANTHROPIC_OUTPUT_MICROUSD_PER_TOKEN")
    entry = get_pricing_entry(model, provider=_pricing_provider(model))
    canonical_unavailable = (
        entry is None
        or entry.input_cost_per_million is None
        or entry.output_cost_per_million is None
    )
    if input_override or output_override:
        # A PARTIAL override must never silently guess the unset side. The old code
        # defaulted the missing rate to a hardcoded Sonnet-ish 3/15 — the one place a
        # price was produced outside the canonical table. Fill the unset side from the
        # EXACT canonical entry instead; if that side is also unpriced, fail closed.
        if (not input_override or not output_override) and canonical_unavailable:
            raise AnthropicPricingUnavailable(
                f"partial Anthropic rate override for model {model!r}, but no exact "
                f"canonical pricing exists to fill the other side"
            )
        input_rate = (
            Decimal(input_override) if input_override
            else entry.input_cost_per_million / _ONE_MILLION  # type: ignore[union-attr]
        )
        output_rate = (
            Decimal(output_override) if output_override
            else entry.output_cost_per_million / _ONE_MILLION  # type: ignore[union-attr]
        )
        return (input_rate, output_rate, "env")
    if canonical_unavailable:
        raise AnthropicPricingUnavailable(
            f"no exact Anthropic pricing is configured for model {model!r}"
        )
    return (
        entry.input_cost_per_million / _ONE_MILLION,
        entry.output_cost_per_million / _ONE_MILLION,
        _anthropic_pricing_source_label(model),
    )


def microusd_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> int:
    """Exact provider cost in microUSD for one Anthropic call. Cached prompt tokens
    bill at their own (cheaper) cache-read / (pricier) cache-write rates from the
    canonical table — never at $0 and never silently at full input rate. The flat
    env override has no separate cache rate, so under the override cached tokens
    bill at the input rate (conservative: never undercharges)."""
    input_override = _env("TAKYON_APP_ANTHROPIC_INPUT_MICROUSD_PER_TOKEN")
    output_override = _env("TAKYON_APP_ANTHROPIC_OUTPUT_MICROUSD_PER_TOKEN")
    if input_override or output_override:
        input_rate, output_rate, _source = anthropic_rates_microusd_per_token(model)
        total = (
            input_rate
            * Decimal(
                max(0, input_tokens)
                + max(0, cache_read_tokens)
                + max(0, cache_write_tokens)
            )
            + output_rate * Decimal(max(0, output_tokens))
        )
        return int(total.to_integral_value(rounding=ROUND_CEILING))

    result = estimate_usage_cost(
        model,
        CanonicalUsage(
            input_tokens=max(0, input_tokens),
            output_tokens=max(0, output_tokens),
            cache_read_tokens=max(0, cache_read_tokens),
            cache_write_tokens=max(0, cache_write_tokens),
        ),
        provider=_pricing_provider(model),
    )
    if result.amount_usd is None:
        raise AnthropicPricingUnavailable(
            f"no exact Anthropic pricing is configured for model {model!r}"
        )
    return int(
        (result.amount_usd * _ONE_MILLION).to_integral_value(rounding=ROUND_CEILING)
    )


def billed_microusd_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> tuple[int, int]:
    """The product usage rail's charge for one Anthropic call: ``(realized, billed)`` in microUSD.

    ``realized`` is the EXACT provider cost from ``microusd_cost`` (unchanged — fail-closed: an
    unpriced model raises ``AnthropicPricingUnavailable`` here, never charges). ``billed`` is the
    customer-facing amount the usage budget reserves/settles against: ``realized`` plus the
    configured usage markup (``usage.markup_bps``, default 25%) via ``usage_pricing.billed_cost``.
    ``billed`` is ALWAYS >= ``realized`` (the customer is never charged below provider cost). The
    caller records BOTH so the ledger keeps money-truth (realized) and revenue-truth (billed)."""
    realized = microusd_cost(
        model,
        input_tokens,
        output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )
    return realized, billed_cost(realized)


def estimate_input_tokens(messages: list[dict], system: str) -> int:
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


def anthropic_payload(body: dict) -> tuple[dict, str, int]:
    model = anthropic_model(body)
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
    return payload, model, estimate_input_tokens(messages, system)


def call_anthropic(payload: dict, api_key: str) -> dict:
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


def openai_key() -> str:
    """The app-rail OpenAI key, resolved server-side.

    Prefer the app-specific rail key when present so operator/CEO usage can keep its own
    ``OPENAI_API_KEY`` while the subuser AI leaf meters against ``TAKYON_APP_OPENAI_API_KEY``.
    """
    return _safebox_env_value("TAKYON_APP_OPENAI_API_KEY", "OPENAI_API_KEY")


def openai_model(body: dict) -> str:
    return _canonical_model_name(
        body.get("model")
        or _env("TAKYON_APP_OPENAI_MODEL")
        or _env("OPENAI_MODEL")
        or "gpt-5.4-mini"
    )


def openai_payload(body: dict) -> tuple[dict, str, int]:
    model = openai_model(body)
    max_tokens = _bounded_int(
        body.get("max_completion_tokens")
        or body.get("max_tokens")
        or body.get("maxTokens"),
        default=1024,
        minimum=1,
        maximum=_bounded_int(
            _env("TAKYON_APP_OPENAI_MAX_TOKENS", "4096"),
            default=4096,
            minimum=1,
            maximum=200_000,
        ),
    )
    system = str(body.get("system") or "").strip()
    raw_messages = body.get("messages")
    messages: list[dict[str, object]] = []
    if system:
        messages.append({"role": "system", "content": system})
    if isinstance(raw_messages, list):
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user").strip()
            if role not in {"system", "user", "assistant"}:
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
    if len(messages) == (1 if system else 0):
        prompt = str(body.get("prompt") or body.get("input") or "").strip()
        if not prompt:
            raise ValueError("prompt or messages is required")
        messages.append({"role": "user", "content": prompt})

    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if body.get("temperature") is not None:
        payload["temperature"] = max(0.0, min(2.0, float(body.get("temperature") or 0)))
    return payload, model, estimate_input_tokens(messages[1:] if system else messages, system)


def _openai_pricing_source_label(model: str) -> str:
    entry = get_pricing_entry(model, provider="openai")
    if entry is None:
        raise OpenAIPricingUnavailable(
            f"no exact OpenAI pricing is configured for model {model!r}"
        )
    parts = [entry.source]
    if entry.pricing_version:
        parts.append(entry.pricing_version)
    return ":".join(part for part in parts if part)


def openai_rates_microusd_per_token(model: str) -> tuple[Decimal, Decimal, str]:
    entry = get_pricing_entry(model, provider="openai")
    if (
        entry is None
        or entry.input_cost_per_million is None
        or entry.output_cost_per_million is None
    ):
        raise OpenAIPricingUnavailable(
            f"no exact OpenAI pricing is configured for model {model!r}"
        )
    return (
        entry.input_cost_per_million / _ONE_MILLION,
        entry.output_cost_per_million / _ONE_MILLION,
        _openai_pricing_source_label(model),
    )


def openai_billed_microusd_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> tuple[int, int]:
    result = estimate_usage_cost(
        model,
        CanonicalUsage(
            input_tokens=max(0, input_tokens),
            output_tokens=max(0, output_tokens),
            cache_read_tokens=max(0, cache_read_tokens),
            cache_write_tokens=max(0, cache_write_tokens),
        ),
        provider="openai",
    )
    if result.amount_usd is None:
        raise OpenAIPricingUnavailable(
            f"no exact OpenAI pricing is configured for model {model!r}"
        )
    realized = int(
        (result.amount_usd * _ONE_MILLION).to_integral_value(rounding=ROUND_CEILING)
    )
    return realized, billed_cost(realized)


def _openai_usage(
    response: dict,
    *,
    estimated_input_tokens: int,
) -> tuple[int, int, int, int]:
    usage = response.get("usage") or {}
    prompt_total = int(usage.get("prompt_tokens") or estimated_input_tokens)
    output_tokens = int(usage.get("completion_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    cache_read_tokens = int(details.get("cached_tokens") or 0)
    cache_write_tokens = int(details.get("cache_write_tokens") or 0)
    input_tokens = max(0, prompt_total - cache_read_tokens - cache_write_tokens)
    return input_tokens, output_tokens, cache_read_tokens, cache_write_tokens


def call_openai(payload: dict, api_key: str) -> dict:
    request = urllib.request.Request(
        OPENAI_CHAT_COMPLETIONS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    timeout = _bounded_int(
        _env("TAKYON_APP_OPENAI_TIMEOUT_SECONDS", "60"),
        default=60,
        minimum=5,
        maximum=300,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API returned {exc.code}: {body[:500]}") from exc


def openai_text(response: dict) -> str:
    message = ((response.get("choices") or [{}])[0] or {}).get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part)
    return ""


def openai_content(response: dict) -> list[dict[str, str]]:
    text = openai_text(response)
    return [{"type": "text", "text": text}] if text else []


def openai_usage(
    response: dict,
    *,
    model: str,
    estimated_input_tokens: int,
) -> dict[str, int | str]:
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens = _openai_usage(
        response,
        estimated_input_tokens=estimated_input_tokens,
    )
    realized, billed = openai_billed_microusd_cost(
        model,
        input_tokens,
        output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "realized_cost_microusd": realized,
        "billed_cost_microusd": billed,
        "provider_request_id": str(response.get("id") or ""),
    }


# ── Tavily web search / extract — product-runtime metered tool provider ──────
# The product runtime reaches Tavily ONLY through the AI gateway's metered search
# broker (`ai_gateway.broker_search_for_business`), never ungated. This leaf prices
# a request fail-closed and performs the call with a key the CALLER passes in
# (resolved by `tavily_key()` server-side; never an app argument, never returned).
# It mirrors the Anthropic leaf above so "what does a search cost / how do we call
# Tavily" lives in ONE place, not two that can drift.

TAVILY_BASE_URL_DEFAULT = "https://api.tavily.com"


class TavilyPricingUnavailable(ValueError):
    """Raised when a Tavily operation has no exact known per-request pricing."""


def normalize_tavily_endpoint_operation(endpoint: str | None, operation: str | None = None) -> tuple[str, str]:
    """Return ``(wire_endpoint, pricing_operation)`` for the Tavily surface Takyon exposes.

    ``search_advanced`` is a priced search depth, not a separate provider URL path. Anything outside
    search/extract fails before a key is attached or a socket opens.
    """
    raw_endpoint = str(endpoint or "").strip("/").lower()
    raw_operation = str(operation or "").strip().lower()
    if not raw_endpoint:
        raw_endpoint = "search"
    if not raw_operation:
        raw_operation = raw_endpoint
    if raw_endpoint == "search_advanced" and raw_operation == "search_advanced":
        raw_endpoint = "search"
    if raw_endpoint == "search" and raw_operation in {"search", "search_advanced"}:
        return "search", raw_operation
    if raw_endpoint == "extract" and raw_operation == "extract":
        return "extract", "extract"
    raise ValueError("unsupported_tavily_operation")


def tavily_key() -> str:
    """The SHARED platform Tavily key, resolved server-side (safebox-aware, then env).
    Returns "" when unconfigured — callers MUST treat "" as blocked, never as permission
    to call keyless (mirrors ``anthropic_key`` / invariant #8)."""
    try:
        from . import safebox

        if safebox.is_sensitive_env_key("TAVILY_API_KEY"):
            value = safebox.read_env_backed_value("TAVILY_API_KEY")
            if value:
                return str(value).strip()
    except Exception:
        pass
    return _env("TAVILY_API_KEY")


def tavily_request_microusd(operation: str, *, units: int = 1) -> int:
    """Exact per-request Tavily cost in microUSD, FAIL-CLOSED. ``operation`` is the pricing
    key ("search" | "search_advanced" | "extract"); ``units`` bills multiple provider
    credits in one request (extract = 1 credit per 5 URLs). Raises ``TavilyPricingUnavailable``
    for any unpriced operation, so an unpriced search can never spend budget — the same
    fail-closed contract the model path has."""
    result = estimate_usage_cost(
        operation,
        CanonicalUsage(request_count=max(1, int(units))),
        provider="tavily",
    )
    if result.amount_usd is None:
        raise TavilyPricingUnavailable(
            f"no exact Tavily pricing is configured for operation {operation!r}"
        )
    return int((result.amount_usd * _ONE_MILLION).to_integral_value(rounding=ROUND_CEILING))


class EgressPricingUnavailable(ValueError):
    """Raised when the generic egress per-request price has no exact known entry."""


# Per-KiB egress data component (µUSD). Egress is free to the platform (the business's own
# credential bears any provider cost), so this is 0 — the flat request_cost in usage_pricing is
# also 0. Set both non-zero only if you want egress to carry a secure-proxy infra markup.
_EGRESS_MICROUSD_PER_KIB = 0


def egress_request_microusd(*, request_bytes: int = 0, response_bytes: int = 0) -> int:
    """Exact per-request egress cost in microUSD, FAIL-CLOSED. A flat platform markup for the
    keyless-egress primitive (priced under ("egress","request") in usage_pricing) plus a small
    per-KiB component over the forwarded request + response bytes. Raises
    ``EgressPricingUnavailable`` if the flat entry is missing, so egress can never spend budget
    unpriced — the same fail-closed contract Tavily/model paths have. Callers reserve on this
    value; per-request providers settle actual==reserved."""
    result = estimate_usage_cost(
        "request",
        CanonicalUsage(request_count=1),
        provider="egress",
    )
    if result.amount_usd is None:
        raise EgressPricingUnavailable("no exact egress pricing is configured")
    flat = int((result.amount_usd * _ONE_MILLION).to_integral_value(rounding=ROUND_CEILING))
    kib = (max(0, int(request_bytes)) + max(0, int(response_bytes)) + 1023) // 1024
    return flat + kib * _EGRESS_MICROUSD_PER_KIB


def call_tavily(endpoint: str, payload: dict, api_key: str) -> dict:
    """Server-side Tavily call with an EXPLICIT key (mirrors ``call_anthropic``). The key is
    injected into the request body and is never returned. Returns the parsed JSON; raises ``RuntimeError`` on HTTP
    error so the broker releases the reservation."""
    endpoint, _operation = normalize_tavily_endpoint_operation(
        endpoint, str((payload or {}).get("operation") or "")
    )
    base_url = (_env("TAVILY_BASE_URL") or TAVILY_BASE_URL_DEFAULT).rstrip("/")
    body = dict(payload or {})
    body["api_key"] = api_key
    headers = {"Content-Type": "application/json"}
    request = urllib.request.Request(
        f"{base_url}/{endpoint}",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    timeout = _bounded_int(
        _env("TAKYON_APP_TAVILY_TIMEOUT_SECONDS", "60"), default=60, minimum=5, maximum=120
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        exc.read()
        raise RuntimeError(f"Tavily API returned {exc.code}") from exc


def anthropic_text(response: dict) -> str:
    parts: list[str] = []
    for item in response.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part)
