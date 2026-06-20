"""Deterministic instant-landing brief floor (anti-thrash rail).

The slow 2-pass dashboard bootstrap was caused by step 2.0 being prompt-driven: when the CEO skipped or
mis-ordered ``business_write_instant_landing``, no brief was pinned, ``src/tokens.css`` stayed byte-identical
to the scaffold placeholder, the placeholder-theme publish gate blocked the first publish, and bootstrap
fell back to the slow design pass. ``_ensure_instant_first_paint_brief`` is the upstream fix: it synthesizes
a minimal truthful brief from canonical state so the first publish ships a real branded page regardless of
CEO tool ordering. These tests lock that rail (and prove it never clobbers a CEO brief or a customized
design-pass landing)."""

import json

import plugins.takyon.core as core


def _seed_scaffold_landing(build_root):
    scaffold = core._subuser_app_scaffold_source_dir() / "src" / "screens" / "landing.tsx"
    dest = build_root / "src" / "screens" / "landing.tsx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(scaffold.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def test_synthesizes_branded_brief_over_seeded_scaffold(tmp_path):
    business_root = tmp_path / "myco"
    build_root = business_root / "product" / "site"
    build_root.mkdir(parents=True, exist_ok=True)
    _seed_scaffold_landing(build_root)

    wrote = core._ensure_instant_first_paint_brief(
        business_root=business_root,
        build_root=build_root,
        surface={"notes": "Truthful one-liner about the product."},
    )

    assert wrote is True
    brief_path = build_root / "instant_landing.json"
    assert brief_path.is_file()
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    # Headline = humanized business name (slug = business_root.name), subhead = surface notes/tagline.
    assert brief["headline"] == "Myco"
    assert brief["subhead"] == "Truthful one-liner about the product."
    assert brief["primary_cta"] == "Continue with Google"


def test_falls_back_to_default_subhead_when_no_surface_notes(tmp_path):
    business_root = tmp_path / "shelf-log"
    build_root = business_root / "product" / "site"
    build_root.mkdir(parents=True, exist_ok=True)
    _seed_scaffold_landing(build_root)

    assert core._ensure_instant_first_paint_brief(
        business_root=business_root, build_root=build_root, surface=None
    ) is True
    brief = json.loads((build_root / "instant_landing.json").read_text(encoding="utf-8"))
    # Multi-token slug is humanized; subhead is a truthful, branded default (never empty/placeholder).
    assert brief["headline"] == "Shelf Log"
    assert "Shelf Log" in brief["subhead"]
    assert brief["subhead"].strip()


def test_respects_an_already_pinned_brief(tmp_path):
    """A CEO/bootstrap brief (richer) must win — the floor only writes when no brief is pinned."""
    business_root = tmp_path / "myco"
    build_root = business_root / "product" / "site"
    build_root.mkdir(parents=True, exist_ok=True)
    _seed_scaffold_landing(build_root)
    pinned = {"headline": "CEO Authored", "subhead": "rich", "primary_cta": "Start", "features": []}
    (build_root / "instant_landing.json").write_text(json.dumps(pinned), encoding="utf-8")

    wrote = core._ensure_instant_first_paint_brief(
        business_root=business_root, build_root=build_root, surface={"notes": "ignored"}
    )

    assert wrote is False
    assert json.loads((build_root / "instant_landing.json").read_text(encoding="utf-8")) == pinned


def test_does_not_synthesize_over_a_customized_landing(tmp_path):
    """Once the design pass customizes landing.tsx, the floor must NOT invent a brief (self-gated to the
    still-seeded scaffold landing, the same gate _apply_instant_first_paint_landing uses)."""
    business_root = tmp_path / "myco"
    build_root = business_root / "product" / "site"
    landing = build_root / "src" / "screens" / "landing.tsx"
    landing.parent.mkdir(parents=True, exist_ok=True)
    landing.write_text("export default function Landing() { return <div>bespoke</div>; }", encoding="utf-8")

    wrote = core._ensure_instant_first_paint_brief(
        business_root=business_root, build_root=build_root, surface={"notes": "x"}
    )

    assert wrote is False
    assert not (build_root / "instant_landing.json").exists()
