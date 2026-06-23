"""Unit tests for the safebox creative-CREDIT-GATE cutover of the brand-logo render.

Covers ``plugins.takyon.creative_gateway._render_logo_png`` — the business-runtime
logo render that must NOT resolve a raw Gemini key and must NOT reserve credits
itself. The creative-credit money gate is AUTHORITATIVE on the safebox now: the
caller has already reserved the ``logo_generate`` credits and holds a creative
capability, which this function presents to the gated ``/v1/providers/gemini/logo``
route. These are the gating + decode contracts only; no network, stdlib + pytest +
monkeypatch.

Contract:
  (a) ``_render_logo_png`` ALWAYS routes through ``safebox.creative_provider_call(
      "gemini", "logo", {"prompt": ...}, token=<capability>)`` and NEVER resolves a
      raw key locally (the raw-key resolver / local SDK call are never touched).
  (b) The gate ``{"image_base64", "format"}`` result is decoded to PNG bytes.
  (c) A result with no image data, or a fail-closed gate error, propagates so the
      caller releases the reservation — never a silent empty return / raw-key fallback.
"""

import base64
import sys
import types

import pytest


def _gw():
    from plugins.takyon import creative_gateway as gw

    return gw


# ─── (a) always through the gated creative route, presenting the capability ───


def test_render_logo_presents_capability_to_gated_route(monkeypatch):
    """``_render_logo_png`` calls ``safebox.creative_provider_call`` with the prompt
    and the creative capability token; the local key resolver and local SDK call are
    NEVER touched (no raw key on this plane)."""
    gw = _gw()
    png = b"\x89PNG\r\n\x1a\nFAKELOGO"

    calls = []

    def _fake_provider_call(provider, path, payload, *, token, **kwargs):
        calls.append((provider, path, dict(payload), token))
        return {"image_base64": base64.b64encode(png).decode("ascii"), "format": "png"}

    def _boom_resolver(*a, **k):  # must not be called — the safebox holds the key
        raise AssertionError("raw key resolver called in the business runtime")

    def _boom_local(*a, **k):  # must not be called — the safebox renders
        raise AssertionError("local Gemini SDK call invoked in the business runtime")

    monkeypatch.setattr(gw.safebox, "creative_provider_call", _fake_provider_call)
    monkeypatch.setattr(gw, "_resolve_gemini_image_key", _boom_resolver)
    monkeypatch.setattr(gw, "_gemini_generate_logo_png", _boom_local)
    monkeypatch.setattr(gw, "_gemini_generate_image_raw", _boom_local)
    # The safebox now returns RAW provider bytes; the runtime keys white -> alpha. Stub that pixel
    # transform to identity so this test asserts the gating/decode contract, not numpy output.
    monkeypatch.setattr(gw, "_key_white_background_to_alpha", lambda data: data)

    out = gw._render_logo_png("draw an icon", capability_token="cap-logo-xyz")

    assert out == png  # (b) decoded from image_base64, then alpha-keyed ON THE RUNTIME (stubbed here)
    assert calls == [("gemini", "logo", {"prompt": "draw an icon"}, "cap-logo-xyz")]


def test_render_logo_empty_payload_raises(monkeypatch):
    """A gate result with no image_base64 fails loudly so the caller releases the
    credit reservation — it must not silently return empty bytes."""
    gw = _gw()
    monkeypatch.setattr(gw.safebox, "creative_provider_call", lambda *a, **k: {"format": "png"})
    monkeypatch.setattr(
        gw,
        "_resolve_gemini_image_key",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("resolver called")),
    )

    with pytest.raises(RuntimeError, match="no image data"):
        gw._render_logo_png("prompt", capability_token="cap")


def test_render_logo_gate_error_propagates(monkeypatch):
    """A fail-closed gate error propagates (never falls back to a raw key)."""
    gw = _gw()

    class _Boom(RuntimeError):
        pass

    def _resolver_must_not_run(*a, **k):
        raise AssertionError("raw key resolver called after gate failure")

    monkeypatch.setattr(
        gw.safebox,
        "creative_provider_call",
        lambda *a, **k: (_ for _ in ()).throw(_Boom("safebox_unreachable")),
    )
    monkeypatch.setattr(gw, "_resolve_gemini_image_key", _resolver_must_not_run)

    with pytest.raises(_Boom):
        gw._render_logo_png("prompt", capability_token="cap")


def test_gemini_logo_generation_uses_current_sdk_parts_shape(monkeypatch):
    """Gemini text-to-image now returns top-level ``response.parts`` in the Python SDK docs.
    The Safebox adapter must use that shape, avoid the older IMAGE-only config override, and emit
    PNG bytes even when the SDK hands back a PIL-like image object."""
    gw = _gw()
    png = b"\x89PNG\r\n\x1a\nPNGDATA"
    captured: dict[str, object] = {}

    class _FakeImage:
        def save(self, fileobj, format):
            captured["saved_format"] = format
            fileobj.write(png)

    class _FakePart:
        text = None
        inline_data = None

        def as_image(self):
            return _FakeImage()

    class _FakeResponse:
        parts = [_FakePart()]

    class _FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse()

    class _FakeClient:
        def __init__(self, *, api_key):
            captured["api_key"] = api_key
            self.models = _FakeModels()

    fake_genai = types.SimpleNamespace(Client=_FakeClient)
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setattr(gw, "_key_white_background_to_alpha", lambda data: data)

    out = gw._gemini_generate_logo_png(api_key="gem-key", prompt="draw a lunchbox")

    assert out == png
    assert captured["api_key"] == "gem-key"
    assert captured["model"] == gw._GEMINI_IMAGE_MODEL
    assert captured["contents"] == ["draw a lunchbox"]
    assert "config" not in captured
    assert captured["saved_format"] == "PNG"


def test_gemini_raw_prefers_inline_bytes_over_as_image(monkeypatch):
    """``_gemini_generate_image_raw`` (the SAFEBOX step) returns the provider's RAW inline bytes
    verbatim when present — it must NOT re-encode via ``as_image()``. The safebox hands back exactly
    what the provider produced; the runtime owns the alpha-key/PNG post-process."""
    gw = _gw()
    raw_inline = b"\xff\xd8raw-jpeg-bytes"
    captured: dict[str, object] = {}

    class _FakeInline:
        data = base64.b64encode(raw_inline).decode("ascii")

    class _FakeImage:
        def save(self, fileobj, format):
            captured["as_image_used"] = True
            fileobj.write(b"\x89PNG\r\n\x1a\nSHOULD-NOT-BE-USED")

    class _FakePart:
        inline_data = _FakeInline()

        def as_image(self):
            return _FakeImage()

    class _FakeResponse:
        parts = [_FakePart()]

    class _FakeModels:
        def generate_content(self, **kwargs):
            return _FakeResponse()

    class _FakeClient:
        def __init__(self, *, api_key):
            self.models = _FakeModels()

    fake_genai = types.SimpleNamespace(Client=_FakeClient)
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)

    out = gw._gemini_generate_image_raw(api_key="gem-key", prompt="draw a lunchbox")

    assert out == raw_inline  # raw provider bytes, returned verbatim by the safebox step
    assert "as_image_used" not in captured  # inline preferred; as_image() never invoked


def test_gemini_logo_generation_postprocesses_inline_bytes_directly(monkeypatch):
    """Some Gemini responses expose only raw inline bytes. Those bytes go straight
    through the alpha postprocessor, which is responsible for decoding and
    returning final PNG bytes."""
    gw = _gw()
    raw_jpeg = b"\xff\xd8raw-inline-jpeg"
    final_png = b"\x89PNG\r\n\x1a\nALPHA"
    seen: dict[str, bytes] = {}

    class _FakeInline:
        data = base64.b64encode(raw_jpeg).decode("ascii")

    class _FakePart:
        inline_data = _FakeInline()

    class _FakeResponse:
        parts = [_FakePart()]

    class _FakeModels:
        def generate_content(self, **kwargs):
            return _FakeResponse()

    class _FakeClient:
        def __init__(self, *, api_key):
            self.models = _FakeModels()

    fake_genai = types.SimpleNamespace(Client=_FakeClient)
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setattr(
        gw,
        "_key_white_background_to_alpha",
        lambda data: seen.setdefault("raw", data) and final_png,
    )

    out = gw._gemini_generate_logo_png(api_key="gem-key", prompt="draw a lunchbox")

    assert out == final_png
    assert seen["raw"] == raw_jpeg
