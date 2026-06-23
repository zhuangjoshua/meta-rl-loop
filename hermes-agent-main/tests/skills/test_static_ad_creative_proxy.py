"""Safebox GATED creative-route cutover for the static-ad-creative-generator backend.

Covers ``skills/takyon/static-ad-creative-generator/scripts/backends.py`` — the
standalone image backend that must NOT read a raw ``OPENAI_API_KEY`` on a runtime
plane. With the gateway-injected creative-gate env set
(``TAKYON_CREATIVE_VIA_PROXY=1`` + ``TAKYON_SAFEBOX_URL`` +
``TAKYON_CREATIVE_CAPABILITY_TOKEN``, with the internal ``TAKYON_SAFEBOX_TOKEN``
optional), ``get_backend`` returns a proxy backend that POSTs the OpenAI body to
the GATED route ``/v1/providers/openai/images`` wrapped as
``{"token": <capability>, "payload": <provider body>}`` (presenting the credit
capability the safebox verifies); without it the local SDK backend (reading
``OPENAI_API_KEY``) is unchanged.

Hermetic: stdlib + pytest + monkeypatch, NO network. The ``urllib`` POST is stubbed
and we assert the raw-key env / OpenAI SDK is never used on the gated path.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "takyon"
    / "static-ad-creative-generator"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

import backends  # noqa: E402


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
    return _PROXY_ENV


def _png_b64() -> str:
    return base64.b64encode(b"\x89PNG\r\n\x1a\nSTATICAD").decode("ascii")


# ─── gated creative-route path ────────────────────────────────────────────────


def test_get_backend_returns_proxy_backend_on_runtime_plane(proxy_env):
    backend = backends.get_backend("openai")
    assert isinstance(backend, backends.ProxyOpenAIImageBackend)


def test_proxy_backend_posts_to_safebox_and_never_reads_key(proxy_env, monkeypatch):
    """The proxy backend POSTs the OpenAI body to the GATED /v1/providers/openai/images,
    decodes b64_json, and never instantiates the OpenAI SDK or reads OPENAI_API_KEY."""
    calls = []

    def _fake_proxy_post(route, payload):
        calls.append((route, payload))
        return {"data": [{"b64_json": _png_b64()}]}

    monkeypatch.setattr(backends, "_proxy_post", _fake_proxy_post)
    # The SDK client must never be touched on the gated path.
    monkeypatch.setattr(
        backends.OpenAIImageBackend,
        "_client",
        lambda self: pytest.fail("OpenAI SDK client built on the gated path"),
    )

    backend = backends.get_backend("openai")
    imgs = backend.generate(prompt="a clean product ad", size="1024x1024", quality="high")

    assert imgs == [b"\x89PNG\r\n\x1a\nSTATICAD"]
    assert len(calls) == 1
    route, payload = calls[0]
    assert route == "/v1/providers/openai/images"
    assert payload["prompt"] == "a clean product ad"
    assert payload["size"] == "1024x1024"


def test_proxy_backend_post_uses_bearer_and_url(proxy_env, monkeypatch):
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
        captured["auth"] = req.headers.get("Authorization")
        captured["data"] = req.data
        return _Resp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    backend = backends.get_backend("openai")
    backend.generate(prompt="p", size="1024x1024")
    assert captured["url"] == "http://10.116.0.2:8000/v1/providers/openai/images"
    assert captured["auth"] == "Bearer internal-token"

    import json

    body = json.loads(captured["data"].decode("utf-8"))
    assert body["token"] == "cap-token-abc"
    assert body["payload"]["prompt"] == "p"


def test_proxy_backend_reference_images_fail_closed(proxy_env):
    """images.edit (reference_images) isn't exposed by the generate-only proxy, so
    the proxy backend fails closed rather than reading a raw key for edits."""
    backend = backends.get_backend("openai")
    with pytest.raises(RuntimeError, match="reference_images .* not supported"):
        backend.generate(prompt="p", size="1024x1024", reference_images=["ref.png"])


def test_proxy_flag_without_coords_fails_closed(monkeypatch):
    monkeypatch.setenv("TAKYON_CREATIVE_VIA_PROXY", "1")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    monkeypatch.delenv("TAKYON_CREATIVE_CAPABILITY_TOKEN", raising=False)
    monkeypatch.delenv("TAKYON_SAFEBOX_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="refusing to fall back to a raw provider key"):
        backends.get_backend("openai")


def test_proxy_http_error_surfaces(proxy_env, monkeypatch):
    import urllib.error
    import urllib.request

    def _boom_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "openai_unconfigured", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _boom_urlopen)
    backend = backends.get_backend("openai")
    with pytest.raises(RuntimeError, match="safebox creative gate .* failed"):
        backend.generate(prompt="p", size="1024x1024")


# ─── local path unchanged (proxy env absent) ──────────────────────────────────


def test_get_backend_local_when_no_proxy(monkeypatch):
    for k in _PROXY_ENV:
        monkeypatch.delenv(k, raising=False)
    backend = backends.get_backend("openai")
    assert isinstance(backend, backends.OpenAIImageBackend)
    assert not isinstance(backend, backends.ProxyOpenAIImageBackend)


def test_local_backend_reads_openai_key(monkeypatch):
    """No proxy env → the SDK backend resolves OPENAI_API_KEY (local dev path)."""
    for k in _PROXY_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local")
    backend = backends.OpenAIImageBackend()
    # _client resolves the key from env; build a fake OpenAI to avoid the real SDK.
    import types

    captured = {}

    def _fake_openai_ctor(api_key=None):
        captured["api_key"] = api_key
        return types.SimpleNamespace()

    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *a, **k):
        if name == "openai":
            return types.SimpleNamespace(OpenAI=_fake_openai_ctor)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    backend._client()
    assert captured["api_key"] == "sk-local"
