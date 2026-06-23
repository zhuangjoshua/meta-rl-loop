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

    out = gw._render_logo_png("draw an icon", capability_token="cap-logo-xyz")

    assert out == png  # (b) decoded from image_base64
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
