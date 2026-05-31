"""Anthropic provider leaf — the one place that builds a Messages request, prices it in microUSD,
calls the SHARED platform provider key, and parses the response.

Extracted from ``app_api.py`` (the SQLite product HTTP surface) so the SAME logic serves BOTH the
current SQLite ``/generate`` route and the Postgres-era Internal AI Gateway (``ai_gateway.py``) —
ONE implementation of "what does a token cost / how do we call Anthropic", never two copies that can
silently drift. ``app_api.py`` imports these back under its existing private names; the gateway
imports the public names directly. When Phase 8 deletes the SQLite path, this leaf is the survivor.

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

from agent.usage_pricing import CanonicalUsage, estimate_usage_cost, get_pricing_entry

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
_ONE_MILLION = Decimal("1000000")


class AnthropicPricingUnavailable(ValueError):
    """Raised when the requested Anthropic model has no exact known pricing."""


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def anthropic_key() -> str:
    """The SHARED platform provider key, resolved server-side. Tries the takyon_cli auth helper
    first, then ANTHROPIC_API_KEY / ANTHROPIC_TOKEN. Returns "" when none is configured — callers
    MUST treat "" as blocked (invariant #8), never as permission to proceed keyless."""
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


def anthropic_model(body: dict) -> str:
    return str(
        body.get("model")
        or _env("TAKYON_APP_ANTHROPIC_MODEL")
        or _env("ANTHROPIC_MODEL")
        or "claude-sonnet-4-6"
    ).strip()


def _anthropic_pricing_source_label(model: str) -> str:
    entry = get_pricing_entry(model, provider="anthropic")
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
    if input_override or output_override:
        return (
            Decimal(input_override or "3"),
            Decimal(output_override or "15"),
            "env",
        )
    entry = get_pricing_entry(model, provider="anthropic")
    if (
        entry is None
        or entry.input_cost_per_million is None
        or entry.output_cost_per_million is None
    ):
        raise AnthropicPricingUnavailable(
            f"no exact Anthropic pricing is configured for model {model!r}"
        )
    return (
        entry.input_cost_per_million / _ONE_MILLION,
        entry.output_cost_per_million / _ONE_MILLION,
        _anthropic_pricing_source_label(model),
    )


def microusd_cost(model: str, input_tokens: int, output_tokens: int) -> int:
    input_override = _env("TAKYON_APP_ANTHROPIC_INPUT_MICROUSD_PER_TOKEN")
    output_override = _env("TAKYON_APP_ANTHROPIC_OUTPUT_MICROUSD_PER_TOKEN")
    if input_override or output_override:
        input_rate, output_rate, _source = anthropic_rates_microusd_per_token(model)
        total = (
            input_rate * Decimal(max(0, input_tokens))
            + output_rate * Decimal(max(0, output_tokens))
        )
        return int(total.to_integral_value(rounding=ROUND_CEILING))

    result = estimate_usage_cost(
        model,
        CanonicalUsage(
            input_tokens=max(0, input_tokens),
            output_tokens=max(0, output_tokens),
        ),
        provider="anthropic",
    )
    if result.amount_usd is None:
        raise AnthropicPricingUnavailable(
            f"no exact Anthropic pricing is configured for model {model!r}"
        )
    return int(
        (result.amount_usd * _ONE_MILLION).to_integral_value(rounding=ROUND_CEILING)
    )


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


def anthropic_text(response: dict) -> str:
    parts: list[str] = []
    for item in response.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part)
