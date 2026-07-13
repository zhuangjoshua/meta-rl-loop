from __future__ import annotations

import binascii
from hashlib import sha256
import json
from pathlib import Path
import struct
import zlib

from plugins.takyon import taste_publication_gate as gate


def _png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload))

    row = b"\x00" + bytes(color) * width
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(row * height, level=9)),
            chunk(b"IEND", b""),
        )
    )


def _write_png(path: Path, width: int, height: int, color: tuple[int, int, int]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _png(width, height, color)
    path.write_bytes(data)
    return sha256(data).hexdigest()


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    (root / "src" / "screens").mkdir(parents=True)
    (root / "src" / "screens" / "landing.tsx").write_text(
        """export function Landing() {
  return <main>
    <section id="hero"><h1>Reports before the next visit</h1><img src="/generated/hero-field.png" /></section>
    <section id="proof"><blockquote>Useful customer proof.</blockquote></section>
    <section id="process"><h2>Finish the report</h2><img src="/generated/process-detail.png" /></section>
    <section id="cta"><form><button>Sign up</button></form></section>
  </main>;
}
""",
        encoding="utf-8",
    )
    (root / "src" / "tokens.css").write_text(
        ":root { --accent: #146080; --surface: #f8fafc; --radius-card: 16px; }\n",
        encoding="utf-8",
    )
    (root / "DESIGN.md").write_text(
        """# Design Read
Reading this as: B2B field-service SaaS for operations teams, with a direct visual language, leaning toward custom Tailwind.

# Foundation
One light theme, one blue accent, and a documented corner radius rule.
DESIGN_VARIANCE: 7
MOTION_INTENSITY: 3
VISUAL_DENSITY: 4
Hero layout desktop: split
Hero layout mobile: stacked
Corner radius rule: interactive controls 12px, cards 16px.
`--accent`: #146080

# Image Plan
- hero: /generated/hero-field.png
- proof: none
- process: /generated/process-detail.png
- cta: none
""",
        encoding="utf-8",
    )
    prompt = (
        "Editorial field-service photography with no baked-in text, UI labels, logos, watermarks, "
        "browser chrome, or fake product controls."
    )
    receipts = root / ".takyon" / "site-images"
    receipts.mkdir(parents=True)
    for index, name in enumerate(("hero-field", "process-detail"), start=1):
        image_path = root / "public" / "generated" / f"{name}.png"
        _write_png(image_path, 1200, 800, (20 * index, 60, 90))
        (receipts / f"{name}.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "public_path": f"/generated/{name}.png",
                    "prompt": prompt,
                }
            ),
            encoding="utf-8",
        )
    return root


def _preflight() -> dict[str, dict[str, object]]:
    return {
        item_id: {
            "passed": True,
            "evidence": f"Verified {item_id} in source, copy, or inspected render.",
            "source": "source-and-browser-inspection",
        }
        for item_id in gate.CANONICAL_PREFLIGHT_IDS
    }


def _section_layouts(*, mobile: bool = False) -> list[dict[str, object]]:
    families = (
        ("media-stack", "quote", "media-stack", "form")
        if mobile
        else ("media-split", "quote", "media-stack", "form")
    )
    return [
        {"key": "hero", "family": families[0], "image_srcs": ["/generated/hero-field.png"], "theme": "light"},
        {"key": "proof", "family": families[1], "image_srcs": [], "theme": "light"},
        {
            "key": "process",
            "family": families[2],
            "image_srcs": ["/generated/process-detail.png"],
            "theme": "light",
        },
        {"key": "cta", "family": families[3], "image_srcs": [], "theme": "light"},
    ]


def _probe(width: int, height: int) -> dict[str, object]:
    mobile = width < 768
    return {
        "viewport_width": width,
        "viewport_height": height,
        "body_text": "Reports before the next visit Useful customer proof Finish the report Sign up Log in",
        "h1_line_count": 2 if not mobile else 3,
        "hero_heading_text": "Reports before the next visit",
        "hero_subtext": "Turn field notes into clear customer reports before the next appointment.",
        "hero_subtext_line_count": 2 if not mobile else 3,
        "hero_price_teasers_after_cta": [],
        "eyebrow_count": 1,
        "eyebrow_labels": ["FIELD SERVICE"],
        "section_count": 4,
        "section_layouts": _section_layouts(mobile=mobile),
        "decorative_dot_count": 0,
        "generic_equal_step_group_count": 0,
        "ctas": [
            {"label": "Sign up", "line_count": 1, "zone": "hero"},
            {"label": "Log in", "line_count": 1, "zone": "header"},
        ],
        "image_srcs": ["/generated/hero-field.png", "/generated/process-detail.png"],
        "header_count": 1,
        "main_count": 1,
        "nested_main_count": 0,
        "document_width": width,
        "scroll_width": width,
        "header_width": width,
        "navigation_height": 72,
        "navigation_line_count": 1,
        "hero_width": width,
        "hero_layout": "stacked" if mobile else "split",
        "hero_heading_top_ratio": 0.2,
        "primary_cta_visible": True,
        "hero_complete": True,
        "next_section_intrusion": False,
        "theme_modes": ["light"],
        "accent_colors": ["rgb(20, 96, 128)"],
        "shape_radii": {"interactive": [12], "cards": [16]},
        "body_font_family": "Geist, sans-serif",
        "body_background_color": "rgb(248, 250, 252)",
    }


def _render(root: Path, width: int, height: int) -> gate.RenderInspection:
    path = root / ".takyon" / "renders" / f"{width}x{height}.png"
    digest = _write_png(path, width, height, (248, 250, 252))
    return gate.RenderInspection(width, height, str(path), digest, True, _probe(width, height))


def _asset_inspections(root: Path) -> dict[str, gate.AssetVisualInspection]:
    results: dict[str, gate.AssetVisualInspection] = {}
    for name in ("hero-field", "process-detail"):
        public_path = f"/generated/{name}.png"
        digest = sha256((root / "public" / "generated" / f"{name}.png").read_bytes()).hexdigest()
        results[public_path] = gate.AssetVisualInspection(
            public_path=public_path,
            image_sha256=digest,
            inspected=True,
            inspected_width=1200,
            inspected_height=800,
            source="safebox-capability:test-inspector",
        )
    return results


def _validate(root: Path, **overrides) -> gate.TasteGateResult:
    arguments = dict(overrides)
    if "desktop" not in arguments:
        arguments["desktop"] = _render(root, *gate.DESKTOP_VIEWPORT)
    if "mobile" not in arguments:
        arguments["mobile"] = _render(root, *gate.MOBILE_VIEWPORT)
    if "asset_inspections" not in arguments:
        arguments["asset_inspections"] = _asset_inspections(root)
    if "preflight_evidence" not in arguments:
        arguments["preflight_evidence"] = _preflight()
    return gate.validate_taste_publication(root, **arguments)


def _codes(result: gate.TasteGateResult) -> set[str]:
    return {finding.code for finding in result.findings}


def test_publication_gate_accepts_complete_desktop_mobile_and_asset_evidence(tmp_path):
    root = _workspace(tmp_path)

    result = _validate(root)

    assert result.passed, result.blocker
    assert result.snapshot is not None


def test_publication_gate_enforces_taste_v2_two_real_image_floor(tmp_path):
    root = _workspace(tmp_path)
    inspections = _asset_inspections(root)
    (root / ".takyon" / "site-images" / "process-detail.json").unlink()
    (root / "public" / "generated" / "process-detail.png").unlink()
    landing = root / "src" / "screens" / "landing.tsx"
    landing.write_text(
        landing.read_text(encoding="utf-8").replace('<img src="/generated/process-detail.png" />', ""),
        encoding="utf-8",
    )
    design = root / "DESIGN.md"
    design.write_text(
        design.read_text(encoding="utf-8").replace("- process: /generated/process-detail.png", "- process: none"),
        encoding="utf-8",
    )
    desktop = _render(root, *gate.DESKTOP_VIEWPORT)
    desktop_probe = dict(desktop.probe)
    desktop_probe["image_srcs"] = ["/generated/hero-field.png"]
    desktop_sections = [dict(entry) for entry in desktop_probe["section_layouts"]]
    desktop_sections[2].update({"family": "text-stack", "image_srcs": []})
    desktop_probe["section_layouts"] = desktop_sections
    desktop = gate.RenderInspection(
        desktop.width,
        desktop.height,
        desktop.screenshot_path,
        desktop.screenshot_sha256,
        True,
        desktop_probe,
    )
    mobile = _render(root, *gate.MOBILE_VIEWPORT)
    mobile_probe = dict(mobile.probe)
    mobile_probe["image_srcs"] = ["/generated/hero-field.png"]
    mobile_sections = [dict(entry) for entry in mobile_probe["section_layouts"]]
    mobile_sections[2]["image_srcs"] = []
    mobile_probe["section_layouts"] = mobile_sections
    mobile = gate.RenderInspection(
        mobile.width,
        mobile.height,
        mobile.screenshot_path,
        mobile.screenshot_sha256,
        True,
        mobile_probe,
    )
    inspections.pop("/generated/process-detail.png")

    result = _validate(root, desktop=desktop, mobile=mobile, asset_inspections=inspections)

    assert "asset_count_invalid" in _codes(result)


def test_publication_gate_rejects_observed_visitbrief_failures(tmp_path):
    root = _workspace(tmp_path)
    landing = root / "src" / "screens" / "landing.tsx"
    landing.write_text(landing.read_text(encoding="utf-8").replace("Useful customer proof.", "Useful customer proof — now."), encoding="utf-8")
    desktop = _render(root, *gate.DESKTOP_VIEWPORT)
    bad_probe = dict(desktop.probe)
    bad_probe.update(
        {
            "body_text": "Broken — visible copy",
            "h1_line_count": 3,
            "hero_subtext": "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty twenty-one",
            "hero_price_teasers_after_cta": ["$19 per month"],
            "decorative_dot_count": 3,
            "eyebrow_count": 4,
            "generic_equal_step_group_count": 1,
            "ctas": [
                {"label": "Sign up", "line_count": 1, "zone": "hero"},
                {"label": "Get started", "line_count": 1, "zone": "footer"},
            ],
            "image_srcs": ["/generated/hero-field.png"],
            "header_count": 2,
            "nested_main_count": 1,
        }
    )
    desktop = gate.RenderInspection(
        desktop.width,
        desktop.height,
        desktop.screenshot_path,
        desktop.screenshot_sha256,
        True,
        bad_probe,
    )
    mobile = _render(root, *gate.MOBILE_VIEWPORT)
    mobile_probe = dict(mobile.probe)
    mobile_probe["image_srcs"] = ["/generated/hero-field.png"]
    mobile = gate.RenderInspection(
        mobile.width,
        mobile.height,
        mobile.screenshot_path,
        mobile.screenshot_sha256,
        True,
        mobile_probe,
    )
    inspections = _asset_inspections(root)
    hero = inspections["/generated/hero-field.png"]
    inspections[hero.public_path] = gate.AssetVisualInspection(
        public_path=hero.public_path,
        image_sha256=hero.image_sha256,
        inspected=True,
        inspected_width=hero.inspected_width,
        inspected_height=hero.inspected_height,
        detected_text=("REPORT 8X?",),
        fake_ui_detected=True,
        artifact_labels=("fabricated tablet controls",),
        source=hero.source,
    )

    result = _validate(root, desktop=desktop, mobile=mobile, asset_inspections=inspections)

    assert {
        "hero_heading_too_many_lines",
        "hero_subtext_too_long",
        "hero_price_teaser_after_cta",
        "decorative_dots",
        "excessive_eyebrows",
        "generic_equal_step_cards",
        "duplicate_cta_intent",
        "visible_dash_forbidden",
        "source_dash_forbidden",
        "asset_baked_text_detected",
        "asset_fake_ui_detected",
        "asset_unused_in_render",
        "wrapper_conflict",
    } <= _codes(result)


def test_publication_gate_fails_closed_on_preflight_layout_and_image_plan(tmp_path):
    root = _workspace(tmp_path)
    evidence = _preflight()
    evidence.pop("copy_self_audit")
    evidence["button_contrast"] = {"passed": False, "evidence": "White on white CTA.", "source": "browser"}
    desktop = _render(root, *gate.DESKTOP_VIEWPORT)
    bad_probe = dict(desktop.probe)
    bad_probe["section_layouts"] = [
        {"key": "hero", "family": "media-split", "image_srcs": ["/generated/hero-field.png"]},
        {"key": "proof", "family": "media-split", "image_srcs": ["/generated/hero-field.png"]},
        {"key": "process", "family": "media-split", "image_srcs": ["/generated/process-detail.png"]},
        {"key": "cta", "family": "form", "image_srcs": []},
    ]
    desktop = gate.RenderInspection(
        desktop.width,
        desktop.height,
        desktop.screenshot_path,
        desktop.screenshot_sha256,
        True,
        bad_probe,
    )

    result = _validate(root, desktop=desktop, preflight_evidence=evidence)

    assert {
        "preflight_evidence_missing",
        "preflight_check_failed",
        "section_layout_diversity_insufficient",
        "section_layout_family_reused",
        "section_layout_repeated_consecutively",
        "image_crop_reused",
        "image_plan_render_contradiction",
    } <= _codes(result)


def test_publication_gate_rejects_prompt_risk_and_design_render_contradictions(tmp_path):
    root = _workspace(tmp_path)
    receipt_path = root / ".takyon" / "site-images" / "hero-field.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["prompt"] = "A tablet screen displaying a polished dashboard report and interface text."
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    tokens = root / "src" / "tokens.css"
    tokens.write_text(tokens.read_text(encoding="utf-8").replace("#146080", "#b71765"), encoding="utf-8")
    desktop = _render(root, *gate.DESKTOP_VIEWPORT)
    bad_probe = dict(desktop.probe)
    bad_probe["hero_layout"] = "centered"
    desktop = gate.RenderInspection(
        desktop.width,
        desktop.height,
        desktop.screenshot_path,
        desktop.screenshot_sha256,
        True,
        bad_probe,
    )
    inspections = _asset_inspections(root)
    hero = inspections["/generated/hero-field.png"]
    inspections[hero.public_path] = gate.AssetVisualInspection(
        public_path=hero.public_path,
        image_sha256=hero.image_sha256,
        inspected=True,
        source=hero.source,
    )

    result = _validate(root, desktop=desktop, asset_inspections=inspections)

    assert {
        "asset_prompt_safety_incomplete",
        "asset_prompt_fake_ui_risk",
        "design_token_contradiction",
        "design_render_contradiction",
        "asset_visual_inspection_not_full_resolution",
    } <= _codes(result)


def test_publication_gate_rejects_incoherent_theme_color_and_shape(tmp_path):
    root = _workspace(tmp_path)
    design = root / "DESIGN.md"
    design.write_text(
        design.read_text(encoding="utf-8").replace("corner radius rule", "corner treatment").replace(
            "Corner radius rule", "Corner treatment"
        ),
        encoding="utf-8",
    )
    desktop = _render(root, *gate.DESKTOP_VIEWPORT)
    bad_probe = dict(desktop.probe)
    bad_probe.update(
        {
            "theme_modes": ["light", "dark"],
            "accent_colors": ["rgb(20, 96, 128)", "rgb(190, 35, 70)"],
            "shape_radii": {"interactive": [8, 18], "cards": [16]},
        }
    )
    desktop = gate.RenderInspection(
        desktop.width,
        desktop.height,
        desktop.screenshot_path,
        desktop.screenshot_sha256,
        True,
        bad_probe,
    )

    result = _validate(root, desktop=desktop)

    assert {"theme_lock_broken", "color_lock_broken", "shape_lock_broken"} <= _codes(result)


def test_design_snapshot_rejects_cross_worker_dilution(tmp_path):
    root = _workspace(tmp_path)
    baseline = gate.capture_design_snapshot(root)
    gate.write_design_snapshot(root, baseline)
    landing = root / "src" / "screens" / "landing.tsx"
    landing.write_text(landing.read_text(encoding="utf-8") + "\n// later worker changed the landing\n", encoding="utf-8")

    result = _validate(root)

    assert "cross_worker_design_dilution" in _codes(result)


def test_asset_inspector_is_injected_digest_bound_and_key_free(tmp_path):
    root = _workspace(tmp_path)
    calls: list[tuple[Path, str]] = []

    def inspector(image_path: Path, receipt: dict[str, object]) -> dict[str, object]:
        calls.append((image_path, str(receipt["public_path"])))
        return {
            "inspected": True,
            "inspected_width": 1200,
            "inspected_height": 800,
            "source": "safebox-capability:test",
        }

    results = gate.run_asset_visual_inspections(root, inspector)
    module_source = Path(gate.__file__).read_text(encoding="utf-8")

    assert set(results) == {"/generated/hero-field.png", "/generated/process-detail.png"}
    assert all(result.image_sha256 for result in results.values())
    assert len(calls) == 2
    assert "GEMINI_API_KEY" not in module_source
    assert "OPENAI_API_KEY" not in module_source
    assert "os.environ" not in module_source
