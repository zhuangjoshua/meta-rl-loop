"""OpenRouter provider profile."""

import json
import logging
import time
from pathlib import Path
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)

_CACHE: list[str] | None = None

# On-disk TTL cache — lets short-lived CLI/cron/gateway processes reuse the
# last catalog fetch instead of re-hitting the OpenRouter /models endpoint on
# every startup.
_DISK_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours


def _disk_cache_path() -> Path | None:
    try:
        from takyon_constants import get_takyon_home

        return get_takyon_home() / "cache" / "openrouter_models.json"
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("openrouter disk cache path unavailable: %s", exc)
        return None


def _read_disk_cache() -> list[str] | None:
    path = _disk_cache_path()
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = float(payload.get("fetched_at", 0))
        models = payload.get("models")
        if not isinstance(models, list):
            return None
        if (time.time() - fetched_at) > _DISK_CACHE_TTL_SECONDS:
            return None
        return [str(m) for m in models]
    except Exception as exc:  # corrupt/unreadable cache — treat as miss
        logger.debug("openrouter disk cache read failed: %s", exc)
        return None


def _write_disk_cache(models: list[str]) -> None:
    path = _disk_cache_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"fetched_at": time.time(), "models": models}),
            encoding="utf-8",
        )
    except Exception as exc:  # non-fatal — cache is best-effort
        logger.debug("openrouter disk cache write failed: %s", exc)


class OpenRouterProfile(ProviderProfile):
    """OpenRouter aggregator — provider preferences, reasoning config passthrough."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Fetch from public OpenRouter catalog — no auth required.

        Note: Tool-call capability filtering is applied by takyon_cli/models.py
        via fetch_openrouter_models() → _openrouter_model_supports_tools(), not
        here. The picker early-returns via the dedicated openrouter path before
        reaching this method, so filtering here would be unreachable.
        """
        global _CACHE  # noqa: PLW0603
        if _CACHE is not None:
            return _CACHE
        disk_cached = _read_disk_cache()
        if disk_cached is not None:
            _CACHE = disk_cached
            return _CACHE
        try:
            result = super().fetch_models(api_key=None, timeout=timeout)
            if result is not None:
                _CACHE = result
                _write_disk_cache(result)
            return result
        except Exception as exc:
            logger.debug("fetch_models(openrouter): %s", exc)
            return None

    def build_extra_body(
        self, *, session_id: str | None = None, **context: Any
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        prefs = context.get("provider_preferences")
        if prefs:
            body["provider"] = prefs

        # Pareto Code router — model-gated. The plugins block is only
        # meaningful for openrouter/pareto-code; sending it on any other
        # model has no documented effect and would be confusing in logs.
        # See: https://openrouter.ai/docs/guides/routing/routers/pareto-router
        model = (context.get("model") or "")
        if model == "openrouter/pareto-code":
            score = context.get("openrouter_min_coding_score")
            if score is not None and score != "":
                try:
                    score_f = float(score)
                except (TypeError, ValueError):
                    score_f = None
                if score_f is not None and 0.0 <= score_f <= 1.0:
                    body["plugins"] = [
                        {"id": "pareto-router", "min_coding_score": score_f}
                    ]
        return body

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        supports_reasoning: bool = False,
        model: str | None = None,
        session_id: str | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """OpenRouter passes the full reasoning_config dict as extra_body.reasoning.

        For xAI Grok models routed through OpenRouter, attach the
        ``x-grok-conv-id`` header so that xAI's prompt cache stays pinned to
        the same backend server across turns.
        """
        extra_body: dict[str, Any] = {}
        if supports_reasoning:
            if reasoning_config is not None:
                extra_body["reasoning"] = dict(reasoning_config)
            else:
                extra_body["reasoning"] = {"enabled": True, "effort": "medium"}

        extra_headers: dict[str, Any] = {}
        if session_id and model and model.startswith(("x-ai/grok-", "xai/grok-")):
            extra_headers["x-grok-conv-id"] = session_id

        return extra_body, {"extra_headers": extra_headers} if extra_headers else {}


openrouter = OpenRouterProfile(
    name="openrouter",
    aliases=("or",),
    env_vars=("OPENROUTER_API_KEY",),
    display_name="OpenRouter",
    description="OpenRouter — unified API for 200+ models",
    signup_url="https://openrouter.ai/keys",
    base_url="https://openrouter.ai/api/v1",
    models_url="https://openrouter.ai/api/v1/models",
    fallback_models=(
        "anthropic/claude-sonnet-4.6",
        "openai/gpt-5.4",
        "deepseek/deepseek-chat",
        "google/gemini-3-flash-preview",
        "qwen/qwen3-plus",
    ),
)

register_provider(openrouter)
