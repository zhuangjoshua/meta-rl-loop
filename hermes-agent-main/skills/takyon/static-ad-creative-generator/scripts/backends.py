"""Image-generation backends.

Default backend: OpenAI **gpt-image-2** (configurable via OPENAI_IMAGE_MODEL). The image
model is the only place that talks to a generation API; everything upstream (intake,
strategy, spec, prompt compilation, QA) is backend-agnostic. To swap backends, implement
``ImageBackend.generate`` and register it in ``get_backend``.

API keys are never hardcoded — the OpenAI SDK reads ``OPENAI_API_KEY`` from the environment.
"""

from __future__ import annotations

import base64
import os
from abc import ABC, abstractmethod
from typing import List, Optional

DEFAULT_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2")

# gpt-image-2 sizing constraints: edges are multiples of 16, each edge <= 3840, aspect within
# 1:3..3:1, total pixels in [655_360, 8_294_400]. We size by fixing the SHORT edge to 1024 and
# scaling the long edge to the requested ratio, which keeps any 1:3..3:1 ratio inside the budget.
_EDGE_MULTIPLE = 16
_MAX_EDGE = 3840
_BASE_SHORT_EDGE = 1024
_MIN_ASPECT, _MAX_ASPECT = 1 / 3, 3.0

# The deprecated gpt-image-1 only supports these three fixed sizes; we snap to the nearest.
GPT_IMAGE_1_SIZES = {"1024x1024": 1.0, "1536x1024": 1.5, "1024x1536": 1024 / 1536}

def parse_ratio(aspect_ratio: str):
    """Parse 'W:H' (decimals allowed, e.g. '1.91:1') into (w, h) floats."""
    try:
        w_str, h_str = str(aspect_ratio).split(":")
        w, h = float(w_str), float(h_str)
        if w <= 0 or h <= 0:
            raise ValueError
        return w, h
    except Exception as exc:
        raise ValueError(
            f"aspect_ratio must look like 'W:H' (e.g. 1:1, 4:5, 1.91:1, 9:16, 16:9); got {aspect_ratio!r}"
        ) from exc


def _round16(x: float) -> int:
    return max(_EDGE_MULTIPLE, int(round(x / _EDGE_MULTIPLE)) * _EDGE_MULTIPLE)


def resolve_size(aspect_ratio: str, model: str = DEFAULT_MODEL) -> str:
    """Compute a concrete model resolution string for any aspect_ratio.

    For gpt-image-2 (and compatible models) the size is computed for the exact ratio. For the
    deprecated gpt-image-1, the ratio is snapped to its nearest supported fixed size (use
    crop_to_aspect afterwards for an exact frame).
    """
    w, h = parse_ratio(aspect_ratio)
    aspect = w / h
    if model.startswith("gpt-image-1"):
        return min(GPT_IMAGE_1_SIZES, key=lambda s: abs(GPT_IMAGE_1_SIZES[s] - aspect))
    if not (_MIN_ASPECT - 1e-9) <= aspect <= (_MAX_ASPECT + 1e-9):
        raise ValueError(
            f"aspect_ratio {aspect_ratio} (={aspect:.3f}) is outside the supported 1:3..3:1 range"
        )
    if aspect >= 1:  # landscape / square: fix the short edge to the height
        height, width = _BASE_SHORT_EDGE, _round16(_BASE_SHORT_EDGE * aspect)
    else:            # portrait: fix the short edge to the width
        width, height = _BASE_SHORT_EDGE, _round16(_BASE_SHORT_EDGE / aspect)
    return f"{min(width, _MAX_EDGE)}x{min(height, _MAX_EDGE)}"


def crop_to_aspect(png_bytes: bytes, aspect_ratio: str) -> bytes:
    """Center-crop an image to an exact target aspect ratio. Needs Pillow; no-op without it."""
    try:
        import io

        from PIL import Image
    except Exception:
        return png_bytes  # Pillow not installed — leave the near-ratio image as-is.

    w_ratio, h_ratio = parse_ratio(aspect_ratio)
    target = w_ratio / h_ratio
    img = Image.open(io.BytesIO(png_bytes))
    w, h = img.size
    current = w / h
    if abs(current - target) < 1e-3:
        return png_bytes
    if current > target:  # too wide -> trim width
        new_w = int(round(h * target))
        left = (w - new_w) // 2
        box = (left, 0, left + new_w, h)
    else:  # too tall -> trim height
        new_h = int(round(w / target))
        top = (h - new_h) // 2
        box = (0, top, w, top + new_h)
    out = io.BytesIO()
    img.crop(box).save(out, format="PNG")
    return out.getvalue()


class ImageBackend(ABC):
    """Backend contract. Return one PNG (bytes) per requested image."""

    name = "abstract"

    @abstractmethod
    def generate(
        self,
        prompt: str,
        size: str,
        n: int = 1,
        reference_images: Optional[List[str]] = None,
        quality: str = "high",
        background: str = "auto",
        output_format: str = "png",
    ) -> List[bytes]:
        ...


class OpenAIImageBackend(ImageBackend):
    """OpenAI Images backend. Default model gpt-image-2 (override with OPENAI_IMAGE_MODEL).

    Uses ``images.generate`` normally, or ``images.edit`` when reference images are supplied
    so the model conditions on brand/reference art. The SDK reads OPENAI_API_KEY from env.
    """

    name = "openai"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: Optional[str] = None):
        self.model = model
        # When provided (e.g. read from --api-key-file), the key is passed straight to the
        # client constructor and never set as an environment variable.
        self.api_key = api_key

    def _client(self):
        key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "No API key available. Pass --api-key-file PATH or set OPENAI_API_KEY."
            )
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover - import guard
            raise RuntimeError("The 'openai' package is required. Install: pip install openai") from exc
        return OpenAI(api_key=key)

    def generate(
        self,
        prompt: str,
        size: str,
        n: int = 1,
        reference_images: Optional[List[str]] = None,
        quality: str = "high",
        background: str = "auto",
        output_format: str = "png",
    ) -> List[bytes]:
        client = self._client()
        if reference_images:
            handles = [open(p, "rb") for p in reference_images]
            try:
                resp = client.images.edit(
                    model=self.model,
                    image=handles,
                    prompt=prompt,
                    size=size,
                    n=n,
                    quality=quality,
                )
            finally:
                for h in handles:
                    h.close()
        else:
            resp = client.images.generate(
                model=self.model,
                prompt=prompt,
                size=size,
                n=n,
                quality=quality,
                background=background,
                output_format=output_format,
            )
        return [base64.b64decode(item.b64_json) for item in resp.data]


def read_api_key_file(path: Optional[str]) -> Optional[str]:
    """Read an API key from a file (first non-empty line), or return None. Never logs it."""
    if not path:
        return None
    with open(os.path.expanduser(path), "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    raise RuntimeError(f"--api-key-file {path!r} contained no key")


def get_backend(name: Optional[str] = None, api_key: Optional[str] = None) -> ImageBackend:
    """Factory. ``name`` selects a registered backend.

    ``api_key``, when given, is handed directly to the backend (not exported to the env).
    """
    name = name or "openai"
    if name == "openai":
        return OpenAIImageBackend(api_key=api_key)
    raise ValueError(f"Unknown backend {name!r}. Registered: openai.")
