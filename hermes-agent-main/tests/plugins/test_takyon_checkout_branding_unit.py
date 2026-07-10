from __future__ import annotations

from plugins.takyon import core


def _site(tmp_path, *, tokens: str, logo: bool = True):
    site = tmp_path / "site"
    (site / "src").mkdir(parents=True)
    (site / "dist").mkdir(parents=True)
    (site / "src" / "tokens.css").write_text(tokens, encoding="utf-8")
    (site / "dist" / "index.html").write_text("ok", encoding="utf-8")
    if logo:
        (site / "dist" / "brand-logo.png").write_bytes(b"\x89PNG\r\n\x1a\nlogo")
    return site


def test_compiler_uses_business_name_literal_tokens_and_published_logo(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    site = _site(
        tmp_path,
        tokens=":root{--tk-primary:#5B21B6;--tk-background:#FAFAFA;--tk-radius:18px;}",
    )

    first = core._compile_stripe_checkout_branding(
        business="climblog",
        display_name="Climb Log",
        source_root=site,
        public_url="https://climblog.coscale.app/",
        live_build_id="build-1",
    )
    second = core._compile_stripe_checkout_branding(
        business="climblog",
        display_name="Climb Log",
        source_root=site,
        public_url="https://climblog.coscale.app/",
        live_build_id="build-1",
    )

    assert first == second
    assert first["source_build_id"] == "build-1"
    assert first["params"] == {
        "branding_settings[display_name]": "Climb Log",
        "branding_settings[background_color]": "#fafafa",
        "branding_settings[button_color]": "#5b21b6",
        "branding_settings[border_style]": "rounded",
        "branding_settings[logo][type]": "url",
        "branding_settings[logo][url]": "https://climblog.coscale.app/brand-logo.png",
        "line_items[0][price_data][product_data][images][0]": (
            "https://climblog.coscale.app/brand-logo.png"
        ),
    }


def test_compiler_omits_invalid_tokens_and_missing_logo(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    site = _site(
        tmp_path,
        tokens=":root{--tk-primary:var(--x);--tk-background:rgb(1,2,3);--tk-radius:2rem;}",
        logo=False,
    )

    snapshot = core._compile_stripe_checkout_branding(
        business="climblog",
        display_name="Climb Log",
        source_root=site,
        public_url="https://climblog.coscale.app/",
        live_build_id="build-2",
    )

    assert snapshot["params"] == {"branding_settings[display_name]": "Climb Log"}


def test_compiler_rejects_cross_business_public_url(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    site = _site(tmp_path, tokens=":root{--tk-primary:#000000;}")

    assert core._compile_stripe_checkout_branding(
        business="climblog",
        display_name="Climb Log",
        source_root=site,
        public_url="https://other.coscale.app/",
        live_build_id="build-3",
    ) == {}


def test_compiler_can_backfill_from_an_existing_published_build(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    build = tmp_path / "published-build"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text("ok", encoding="utf-8")
    (build / "brand-logo.png").write_bytes(b"\x89PNG\r\n\x1a\nlogo")
    (build / "assets" / "index.css").write_text(
        ":root{--tk-primary:#123456;--tk-background:#ffffff;--tk-radius:9999px}",
        encoding="utf-8",
    )

    snapshot = core._compile_stripe_checkout_branding(
        business="climblog",
        display_name="Climb Log",
        source_root=build,
        public_url="https://climblog.coscale.app/",
        live_build_id="build-4",
    )

    assert snapshot["params"]["branding_settings[button_color]"] == "#123456"
    assert snapshot["params"]["branding_settings[border_style]"] == "pill"
    assert snapshot["params"]["branding_settings[logo][type]"] == "url"
