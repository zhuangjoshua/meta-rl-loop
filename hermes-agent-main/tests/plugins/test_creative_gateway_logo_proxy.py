"""Unit tests for the safebox-proxy cutover of the brand-logo render.

Covers ``plugins.takyon.creative_gateway._render_logo_png`` — the business-runtime
logo render that must NOT resolve a raw Gemini key on a runtime plane. These are
the gating + decode contracts only; no network, stdlib + pytest + monkeypatch.

Contract:
  (a) When ``safebox._use_remote_authority()`` is True (runtime plane), the
      render goes through ``safebox.proxy_request("gemini", "image", ...)`` and
      NEVER calls the local key resolver or the local Gemini SDK call.
  (b) When it is False (safebox host / local dev), the existing local path is
      used unchanged: resolve the key, call ``_gemini_generate_logo_png``.
  (c) The proxy ``{"image_base64", "format"}`` result is decoded to PNG bytes.
"""

import base64

import pytest


def _gw():
    from plugins.takyon import creative_gateway as gw

    return gw


# ─── (a) runtime plane → proxy, no raw key ────────────────────────────────────


def test_render_logo_uses_proxy_on_runtime_plane(monkeypatch):
    """Remote authority → proxy_request is called with the prompt; the local key
    resolver and local SDK call are NEVER touched."""
    gw = _gw()
    png = b"\x89PNG\r\n\x1a\nFAKELOGO"

    proxy_calls = []

    def _fake_proxy(provider, path, payload, **kwargs):
        proxy_calls.append((provider, path, dict(payload)))
        return {"image_base64": base64.b64encode(png).decode("ascii"), "format": "png"}

    def _boom_resolver(*a, **k):  # must not be called on the runtime plane
        raise AssertionError("raw key resolver called on runtime plane")

    def _boom_local(*a, **k):  # must not be called on the runtime plane
        raise AssertionError("local Gemini SDK call invoked on runtime plane")

    monkeypatch.setattr(gw, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(gw.safebox, "proxy_request", _fake_proxy)
    monkeypatch.setattr(gw, "_resolve_gemini_image_key", _boom_resolver)
    monkeypatch.setattr(gw, "_gemini_generate_logo_png", _boom_local)

    out = gw._render_logo_png("draw an icon")

    assert out == png  # (c) decoded from image_base64
    assert proxy_calls == [("gemini", "image", {"prompt": "draw an icon"})]


def test_render_logo_proxy_empty_payload_raises(monkeypatch):
    """A proxy result with no image_base64 fails loudly so the caller releases
    the credit reservation — it must not silently return empty bytes."""
    gw = _gw()
    monkeypatch.setattr(gw, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(gw.safebox, "proxy_request", lambda *a, **k: {"format": "png"})
    monkeypatch.setattr(
        gw,
        "_resolve_gemini_image_key",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("resolver called")),
    )

    with pytest.raises(RuntimeError, match="no image data"):
        gw._render_logo_png("prompt")


def test_render_logo_proxy_error_propagates(monkeypatch):
    """A fail-closed proxy error propagates (never falls back to a raw key)."""
    gw = _gw()

    class _Boom(RuntimeError):
        pass

    def _resolver_must_not_run(*a, **k):
        raise AssertionError("raw key resolver called after proxy failure")

    monkeypatch.setattr(gw, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(
        gw.safebox,
        "proxy_request",
        lambda *a, **k: (_ for _ in ()).throw(_Boom("safebox_unreachable")),
    )
    monkeypatch.setattr(gw, "_resolve_gemini_image_key", _resolver_must_not_run)

    with pytest.raises(_Boom):
        gw._render_logo_png("prompt")


# ─── (b) local / safebox authority → local path unchanged ─────────────────────


def test_render_logo_uses_local_path_when_not_remote(monkeypatch):
    """No remote authority → resolve the key locally and call the local SDK
    render; the proxy is NEVER touched."""
    gw = _gw()
    png = b"\x89PNGLOCAL"

    local_calls = []

    def _fake_local(*, api_key, prompt):
        local_calls.append((api_key, prompt))
        return png

    def _boom_proxy(*a, **k):  # must not be called on the local plane
        raise AssertionError("proxy_request called on the safebox/local plane")

    monkeypatch.setattr(gw, "_use_remote_authority", lambda: False)
    monkeypatch.setattr(gw, "_resolve_gemini_image_key", lambda: "sk-local-key")
    monkeypatch.setattr(gw, "_gemini_generate_logo_png", _fake_local)
    monkeypatch.setattr(gw.safebox, "proxy_request", _boom_proxy)

    out = gw._render_logo_png("draw an icon")

    assert out == png
    assert local_calls == [("sk-local-key", "draw an icon")]


def test_render_logo_local_unconfigured_fails_closed(monkeypatch):
    """No remote authority AND no provisioned key → 503 before any provider work."""
    from fastapi import HTTPException

    gw = _gw()
    monkeypatch.setattr(gw, "_use_remote_authority", lambda: False)
    monkeypatch.setattr(gw, "_resolve_gemini_image_key", lambda: "")
    monkeypatch.setattr(
        gw,
        "_gemini_generate_logo_png",
        lambda **k: (_ for _ in ()).throw(AssertionError("rendered without a key")),
    )

    with pytest.raises(HTTPException) as exc:
        gw._render_logo_png("prompt")
    assert exc.value.status_code == 503
    assert exc.value.detail == "gemini_image_unconfigured"
