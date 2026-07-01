"""OpenAI Sora video generation backend.

This provider plugs into the existing ``video_generate`` rail. It does not
create a separate Takyon tool: the operator picks ``video_gen.provider=openai``
and normal business creative generation calls flow through the same guarded
budget/receipt path as FAL and xAI.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from takyon_cli.config import get_env_value

from agent.video_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_RESOLUTION,
    VideoGenProvider,
    error_response,
    save_stream_video,
    success_response,
)

logger = logging.getLogger(__name__)


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "sora-2"
DEFAULT_SECONDS = 4
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_POLL_INTERVAL_SECONDS = 10

_MODELS: Dict[str, Dict[str, Any]] = {
    "sora-2": {
        "display": "Sora 2",
        "speed": "minutes",
        "strengths": "Fast iteration, social/video concepts, synced audio",
        "price": "paid",
        "modalities": ["text", "image"],
    },
    "sora-2-pro": {
        "display": "Sora 2 Pro",
        "speed": "minutes+",
        "strengths": "Higher-fidelity marketing assets and polished shots",
        "price": "premium",
        "modalities": ["text", "image"],
    },
}

_VALID_SECONDS = (4, 8, 12)


def _load_local_env() -> None:
    try:
        from plugins.takyon.core import load_takyon_env

        load_takyon_env()
    except Exception:
        try:
            from takyon_cli.env_loader import load_takyon_dotenv

            load_takyon_dotenv()
        except Exception:
            pass


def _load_video_gen_section() -> Dict[str, Any]:
    try:
        from takyon_cli.config import load_config

        cfg = load_config()
        section = cfg.get("video_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load video_gen config: %s", exc)
        return {}


def _resolve_model(explicit: Optional[str]) -> str:
    candidates: List[Optional[str]] = [
        explicit,
        os.getenv("OPENAI_VIDEO_MODEL"),
    ]
    cfg = _load_video_gen_section()
    openai_cfg = cfg.get("openai") if isinstance(cfg.get("openai"), dict) else {}
    if isinstance(openai_cfg, dict):
        candidates.append(openai_cfg.get("model"))
    top = cfg.get("model")
    if isinstance(top, str):
        candidates.append(top)

    for candidate in candidates:
        value = str(candidate or "").strip()
        if value in _MODELS:
            return value
    return DEFAULT_MODEL


def _resolve_client() -> Tuple[str, str]:
    _load_local_env()
    api_key = str(get_env_value("OPENAI_API_KEY") or "").strip()
    base_url = (
        os.getenv("OPENAI_VIDEO_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or DEFAULT_BASE_URL
    ).strip().rstrip("/")
    return api_key, base_url


def _nearest_seconds(duration: Optional[int]) -> int:
    try:
        value = int(duration if duration is not None else DEFAULT_SECONDS)
    except (TypeError, ValueError):
        value = DEFAULT_SECONDS
    return min(_VALID_SECONDS, key=lambda allowed: abs(allowed - value))


def _resolve_size(aspect_ratio: str, resolution: str) -> Tuple[str, str, str]:
    ratio = (aspect_ratio or DEFAULT_ASPECT_RATIO).strip()
    res = (resolution or DEFAULT_RESOLUTION).strip().lower()
    if ratio == "9:16":
        return ("1024x1792" if res == "1080p" else "720x1280", "9:16", "1080p" if res == "1080p" else "720p")
    return ("1792x1024" if res == "1080p" else "1280x720", "16:9", "1080p" if res == "1080p" else "720p")


def _json_request(
    method: str,
    url: str,
    *,
    api_key: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    data = None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "takyon-agent/video_gen/openai",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
        except Exception:
            pass
        raise RuntimeError(f"OpenAI Videos API {method} failed ({exc.code}): {detail or exc.reason}") from exc
    return json.loads(raw) if raw else {}


def _download_video(video_id: str, *, api_key: str, base_url: str, prefix: str) -> str:
    request = urllib.request.Request(
        f"{base_url}/videos/{urllib.parse.quote(video_id)}/content",
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "takyon-agent/video_gen/openai",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            saved_path = save_stream_video(response, prefix=prefix)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
        except Exception:
            pass
        raise RuntimeError(f"OpenAI video download failed ({exc.code}): {detail or exc.reason}") from exc
    return str(saved_path)


class OpenAIVideoGenProvider(VideoGenProvider):
    """OpenAI Sora backend for the unified ``video_generate`` tool."""

    @property
    def name(self) -> str:
        return "openai"

    @property
    def display_name(self) -> str:
        return "OpenAI Sora"

    def is_available(self) -> bool:
        api_key, _ = _resolve_client()
        return bool(api_key)

    def list_models(self) -> List[Dict[str, Any]]:
        return [{"id": model_id, **meta} for model_id, meta in _MODELS.items()]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "OpenAI Sora",
            "badge": "paid",
            "tag": "sora-2 / sora-2-pro via OPENAI_API_KEY",
            "env_vars": [
                {
                    "key": "OPENAI_API_KEY",
                    "prompt": "OpenAI API key",
                    "url": "https://platform.openai.com/api-keys",
                },
            ],
        }

    def capabilities(self) -> Dict[str, Any]:
        return {
            "modalities": ["text", "image"],
            "aspect_ratios": ["16:9", "9:16"],
            "resolutions": ["720p", "1080p"],
            "max_duration": 12,
            "min_duration": 4,
            "supports_audio": True,
            "supports_negative_prompt": False,
            "max_reference_images": 1,
        }

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        duration: Optional[int] = None,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        resolution: str = DEFAULT_RESOLUTION,
        negative_prompt: Optional[str] = None,
        audio: Optional[bool] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        if not prompt:
            return error_response(
                error="prompt is required for OpenAI Sora video generation",
                error_type="missing_prompt",
                provider="openai",
                prompt=prompt,
            )

        api_key, base_url = _resolve_client()
        if not api_key:
            return error_response(
                error=(
                    "OPENAI_API_KEY not set. Add it to the Takyon env file or "
                    "run `takyon tools` -> Video Generation -> OpenAI Sora."
                ),
                error_type="auth_required",
                provider="openai",
                prompt=prompt,
            )

        model_id = _resolve_model(model)
        seconds = _nearest_seconds(duration)
        size, normalized_ratio, normalized_resolution = _resolve_size(aspect_ratio, resolution)
        ref_url = (image_url or "").strip()
        refs = [str(url).strip() for url in (reference_image_urls or []) if str(url).strip()]
        if ref_url and refs:
            return error_response(
                error="OpenAI Sora accepts one image reference; pass image_url or one reference_image_urls item, not both.",
                error_type="conflicting_inputs",
                provider="openai",
                model=model_id,
                prompt=prompt,
            )
        if len(refs) > 1:
            return error_response(
                error="OpenAI Sora accepts at most one reference image in this unified video_generate surface.",
                error_type="too_many_references",
                provider="openai",
                model=model_id,
                prompt=prompt,
            )
        ref_url = ref_url or (refs[0] if refs else "")

        payload: Dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "seconds": str(seconds),
            "size": size,
        }
        if ref_url:
            payload["input_reference"] = {"image_url": ref_url}

        timeout_seconds = int(os.getenv("OPENAI_VIDEO_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
        poll_interval = int(os.getenv("OPENAI_VIDEO_POLL_INTERVAL_SECONDS") or DEFAULT_POLL_INTERVAL_SECONDS)

        try:
            created = _json_request(
                "POST",
                f"{base_url}/videos",
                api_key=api_key,
                payload=payload,
                timeout=90,
            )
            video_id = str(created.get("id") or "").strip()
            if not video_id:
                return error_response(
                    error="OpenAI returned no video id",
                    error_type="empty_response",
                    provider="openai",
                    model=model_id,
                    prompt=prompt,
                )

            started = time.monotonic()
            status_body = created
            status = str(status_body.get("status") or "").lower()
            while status not in {"completed", "failed"}:
                if time.monotonic() - started > timeout_seconds:
                    return error_response(
                        error=f"Timed out waiting for OpenAI video generation after {timeout_seconds}s",
                        error_type="timeout",
                        provider="openai",
                        model=model_id,
                        prompt=prompt,
                    )
                time.sleep(max(1, poll_interval))
                status_body = _json_request(
                    "GET",
                    f"{base_url}/videos/{urllib.parse.quote(video_id)}",
                    api_key=api_key,
                    timeout=60,
                )
                status = str(status_body.get("status") or "").lower()

            if status == "failed":
                error = status_body.get("error") if isinstance(status_body.get("error"), dict) else {}
                message = error.get("message") or status_body.get("message") or "OpenAI video generation failed"
                return error_response(
                    error=str(message),
                    error_type=str(error.get("code") or "openai_failed"),
                    provider="openai",
                    model=model_id,
                    prompt=prompt,
                )

            saved_path = _download_video(
                video_id,
                api_key=api_key,
                base_url=base_url,
                prefix=f"openai_{model_id}",
            )
        except Exception as exc:
            logger.warning("OpenAI Sora video generation failed: %s", exc, exc_info=True)
            return error_response(
                error=f"OpenAI Sora video generation failed: {exc}",
                error_type="api_error",
                provider="openai",
                model=model_id,
                prompt=prompt,
            )

        extra: Dict[str, Any] = {
            "video_id": video_id,
            "size": status_body.get("size") or size,
            "seconds": status_body.get("seconds") or str(seconds),
            "status": status_body.get("status") or status,
            "resolution": normalized_resolution,
        }
        if status_body.get("expires_at"):
            extra["expires_at"] = status_body["expires_at"]
        if status_body.get("progress") is not None:
            extra["progress"] = status_body["progress"]

        return success_response(
            video=str(saved_path),
            model=str(status_body.get("model") or model_id),
            prompt=prompt,
            modality="image" if ref_url else "text",
            aspect_ratio=normalized_ratio,
            duration=int(status_body.get("seconds") or seconds),
            provider="openai",
            extra=extra,
        )


def register(ctx) -> None:
    """Plugin entry point: register the OpenAI Sora video provider."""
    ctx.register_video_gen_provider(OpenAIVideoGenProvider())
