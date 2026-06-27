"""Safebox creative-gate cutover for the ugc-video-ad pipeline subprocess.

Covers ``skills/takyon/ugc-video-ad/scripts/pipeline.py`` — the standalone render
subprocess that must NOT read a raw ``OPENAI_API_KEY`` / ``FAL_KEY`` on a runtime
plane. With the gateway-injected creative-gate env set
(``TAKYON_CREATIVE_VIA_PROXY=1`` + ``TAKYON_SAFEBOX_URL`` +
``TAKYON_CREATIVE_CAPABILITY_TOKEN``, plus an OPTIONAL ``TAKYON_SAFEBOX_TOKEN``),
the OpenAI image call and the FAL Kling call route through the GATED creative
routes (``/v1/providers/openai/images``, ``/v1/providers/fal/kling-image-to-video``), presenting
the creative capability in the request body; without that env, the local path is
explicit-key only.

Hermetic: stdlib + pytest + monkeypatch, NO network. The ``urllib`` POST is stubbed
and we assert the gate was hit (with the capability in the body) AND that the
raw-key env was never read.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "takyon"
    / "ugc-video-ad"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

import pipeline  # noqa: E402


_PROXY_ENV = {
    "TAKYON_CREATIVE_VIA_PROXY": "1",
    "TAKYON_SAFEBOX_URL": "http://10.116.0.2:8000",
    "TAKYON_SAFEBOX_TOKEN": "internal-token",
    "TAKYON_CREATIVE_CAPABILITY_TOKEN": "cap-token-abc",
}


@pytest.fixture
def proxy_env(monkeypatch):
    for k, v in _PROXY_ENV.items():
        monkeypatch.setenv(k, v)
    # A raw key in the env must NEVER be consulted on the proxy path; if it is, the test fails.
    monkeypatch.setattr(
        pipeline,
        "require_explicit_secret",
        lambda name, value: pytest.fail(f"require_explicit_secret({name!r}) ran on the proxy path"),
    )
    return _PROXY_ENV


def _png_b64() -> str:
    return base64.b64encode(b"\x89PNG\r\n\x1a\nUGCREF").decode("ascii")


# ─── proxy path: OpenAI image ─────────────────────────────────────────────────


def test_generate_image_uses_openai_proxy_on_runtime_plane(proxy_env, monkeypatch, tmp_path):
    """Runtime plane → POST /v1/providers/openai/images through the GATED creative
    route; the raw key is never read and the b64 image is written to disk."""
    calls = []

    def _fake_proxy_post(route, payload):
        calls.append((route, payload))
        return {"data": [{"b64_json": _png_b64()}]}

    monkeypatch.setattr(pipeline, "_proxy_post", _fake_proxy_post)

    out = tmp_path / "reference.png"
    result = pipeline.generate_image("a candid selfie", str(out), size="864x1536")

    assert result == str(out)
    assert out.read_bytes() == b"\x89PNG\r\n\x1a\nUGCREF"
    assert len(calls) == 1
    route, payload = calls[0]
    assert route == "/v1/providers/openai/images"
    assert payload["prompt"] == "a candid selfie"
    assert payload["size"] == "864x1536"


def test_proxy_post_uses_bearer_and_safebox_url(proxy_env, monkeypatch, tmp_path):
    """The low-level creative-gate POST targets the injected safebox GATED route with
    the internal Bearer token, carries the creative capability in the JSON body, and
    never reaches OpenAI/FAL directly."""
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            import json

            return json.dumps({"data": [{"b64_json": _png_b64()}]}).encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["auth"] = req.headers.get("Authorization")
        captured["data"] = req.data
        return _Resp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    out = tmp_path / "ref.png"
    pipeline.generate_image("prompt", str(out))

    assert captured["url"] == "http://10.116.0.2:8000/v1/providers/openai/images"
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer internal-token"

    import json

    body = json.loads(captured["data"].decode("utf-8"))
    assert body["token"] == "cap-token-abc"
    assert body["payload"]["prompt"] == "prompt"


# ─── proxy path: FAL Kling ────────────────────────────────────────────────────


def _no_fal_client_import(monkeypatch):
    """Make ``import fal_client`` fail loudly so a test proves the proxy path never
    touches the keyed FAL client. Captures the real import first to avoid recursion."""
    import builtins

    real_import = builtins.__import__

    def _guard(name, *a, **k):
        if name == "fal_client":
            pytest.fail("fal_client imported on the proxy path")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _guard)


def test_upload_image_data_uri_on_proxy_no_fal_key(proxy_env, monkeypatch, tmp_path):
    """A local file becomes a base64 data URI on the proxy path — no fal_client
    upload and no FAL_KEY read."""
    _no_fal_client_import(monkeypatch)

    img = tmp_path / "frame.png"
    img.write_bytes(b"\x89PNGrawbytes")
    uri = pipeline.upload_image(str(img))
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == b"\x89PNGrawbytes"


def test_upload_image_passes_through_urls(proxy_env):
    assert pipeline.upload_image("https://cdn/x.png") == "https://cdn/x.png"
    assert pipeline.upload_image("data:image/png;base64,AAAA") == "data:image/png;base64,AAAA"


def test_generate_clip_uses_fixed_fal_proxy_on_runtime_plane(proxy_env, monkeypatch):
    """Runtime plane → POST /v1/providers/fal/kling-image-to-video through the GATED creative
    route; the verbatim FAL JSON ({"video":{"url":...}}) is read for the URL and no
    FAL_KEY is consulted."""
    calls = []

    def _fake_proxy_post(route, payload):
        calls.append((route, payload))
        return {"video": {"url": "https://fal-cdn/out.mp4"}}

    monkeypatch.setattr(pipeline, "_proxy_post", _fake_proxy_post)
    _no_fal_client_import(monkeypatch)

    url = pipeline.generate_clip(
        "data:image/png;base64,AAAA",
        "speak to camera",
        7,
        endpoint="fal-ai/kling-video/v3/pro/image-to-video",
    )

    assert url == "https://fal-cdn/out.mp4"
    assert len(calls) == 1
    route, payload = calls[0]
    assert route == "/v1/providers/fal/kling-image-to-video"
    assert payload["start_image_url"] == "data:image/png;base64,AAAA"
    assert payload["duration"] == "7"


def test_generate_clip_proxy_ignores_caller_supplied_endpoint(proxy_env, monkeypatch):
    calls = []
    monkeypatch.setattr(
        pipeline,
        "_proxy_post",
        lambda route, payload: calls.append((route, payload)) or {"video": {"url": "https://fal-cdn/out.mp4"}},
    )
    _no_fal_client_import(monkeypatch)

    pipeline.generate_clip(
        "data:image/png;base64,AAAA",
        "speak",
        5,
        endpoint="http://169.254.169.254/latest/meta-data",
    )

    assert calls[0][0] == "/v1/providers/fal/kling-image-to-video"


def test_generate_clip_proxy_no_video_url_raises(proxy_env, monkeypatch):
    monkeypatch.setattr(pipeline, "_proxy_post", lambda route, payload: {"video": {}})
    with pytest.raises(RuntimeError, match="no video url"):
        pipeline.generate_clip("data:image/png;base64,AAAA", "p", 5)


# ─── fail-closed ──────────────────────────────────────────────────────────────


def test_proxy_flag_without_coords_fails_closed(monkeypatch, tmp_path):
    """Flag set but URL/capability missing → raise, never fall back to a raw key."""
    monkeypatch.setenv("TAKYON_CREATIVE_VIA_PROXY", "1")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    monkeypatch.delenv("TAKYON_CREATIVE_CAPABILITY_TOKEN", raising=False)
    monkeypatch.delenv("TAKYON_SAFEBOX_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="refusing to fall back to a raw provider key"):
        pipeline.generate_image("p", str(tmp_path / "x.png"))


def test_proxy_http_error_surfaces(proxy_env, monkeypatch, tmp_path):
    """A proxy HTTP error propagates (no raw-key fallback)."""
    import urllib.error

    import urllib.request

    def _boom_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "fal_unconfigured", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _boom_urlopen)
    with pytest.raises(RuntimeError, match="safebox creative gate .* failed"):
        pipeline.generate_image("p", str(tmp_path / "x.png"))


# ─── local path unchanged (proxy env absent) ──────────────────────────────────


def test_local_path_uses_explicit_openai_key_when_no_proxy(monkeypatch, tmp_path):
    """No proxy env -> the direct OpenAI httpx path requires an explicit key."""
    for k in _PROXY_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-must-not-be-read")

    posted = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": [{"b64_json": _png_b64()}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            posted["url"] = url
            posted["auth"] = headers["Authorization"]
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "Client", _Client)
    # The proxy POST must NOT be used on the local path.
    monkeypatch.setattr(
        pipeline, "_proxy_post", lambda *a, **k: pytest.fail("proxy used on local path")
    )

    out = tmp_path / "ref.png"
    pipeline.generate_image("p", str(out), api_key="sk-local-explicit")
    assert out.read_bytes() == b"\x89PNG\r\n\x1a\nUGCREF"
    assert posted["url"].endswith("/images/generations")
    assert posted["auth"] == "Bearer sk-local-explicit"


def test_local_path_does_not_read_openai_key_env(monkeypatch, tmp_path):
    for k in _PROXY_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-must-not-be-read")
    with pytest.raises(RuntimeError, match="explicit local key file"):
        pipeline.generate_image("p", str(tmp_path / "ref.png"))


def test_load_dotenv_skips_provider_secret_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=sk-env-must-not-load\nFAL_KEY=fal-must-not-load\nOPENAI_BASE_URL=https://example.test/v1\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    pipeline.load_dotenv(str(env_file))

    assert "OPENAI_API_KEY" not in os.environ
    assert "FAL_KEY" not in os.environ
    assert os.environ["OPENAI_BASE_URL"] == "https://example.test/v1"
