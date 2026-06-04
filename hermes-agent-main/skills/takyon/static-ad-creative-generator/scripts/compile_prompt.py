"""Deterministic prompt compiler: ad spec -> art-directed image-model brief.

Contract (see references/prompt-compiler-rules.md): this module TRANSLATES an already
approved ad spec into image-model instructions. It never invents strategy — angle, hook,
audience, and proof are read from the spec, not chosen here. Same spec in => same prompt out.
"""

from __future__ import annotations

import json
import sys
from typing import List, Optional

# Section labels, in fixed emission order. Keep in sync with prompt-compiler-rules.md.
SECTION_ORDER = [
    "OBJECTIVE",
    "PLATFORM & PLACEMENT",
    "FORMAT",
    "VISUAL STYLE",
    "SCENE & SUBJECT",
    "COMPOSITION",
    "LIGHTING & BACKGROUND",
    "PRODUCT",
    "OVERLAY TEXT",
    "TYPOGRAPHY & LAYOUT",
    "BRAND CONSTRAINTS",
    "REALISM",
    "AVOID",
]

_REALISM = (
    "Photographic, real-world rendering. Exact quantities of every object and person. "
    "No garbled, misspelled, or invented text. No extra fingers, hands, or limbs. "
    "Clean, intentional, on-brief — not a busy collage."
)


def _join(items: Optional[List[str]]) -> str:
    return ", ".join(str(i).strip() for i in (items or []) if str(i).strip())


def _s(text: str) -> str:
    """Normalize a fragment into a sentence: trim, capitalize a plain lowercase start,
    and ensure terminal punctuation. Leaves mixed-case tokens (e.g. 'iPhone') untouched."""
    text = (text or "").strip()
    if not text:
        return text
    first = text.split(" ", 1)[0]
    if text[0].islower() and not any(c.isupper() for c in first):
        text = text[0].upper() + text[1:]
    if text[-1] not in ".!?:":
        text += "."
    return text


def compile_prompt(spec: dict, size: Optional[str] = None) -> str:
    """Return the compiled image-model prompt for one ad spec.

    ``size`` is the resolved model resolution (e.g. "1024x1280"); if omitted, only the
    aspect ratio is stated in the FORMAT section.
    """
    aud = spec.get("audience", {})
    strat = spec.get("strategy", {})
    vis = spec.get("visual", {})
    prod = spec.get("product", {})
    copy = spec.get("copy", {})
    lay = spec.get("layout", {})
    pr = spec.get("prompting", {})

    sections: dict = {}

    # 1. OBJECTIVE — guidance for art direction, not text to render.
    hook = (strat.get("hook", "") or "").strip().rstrip(".")
    promise = (strat.get("promise", "") or "").strip().rstrip(".")
    boldness = (strat.get("boldness", "") or "").strip()
    disruption_target = (strat.get("disruption_target", "") or "").strip()
    hook_tactic = (strat.get("hook_tactic", "") or "").strip()
    trigger = (strat.get("psychological_trigger", "") or "").strip()
    mechanic = (strat.get("creative_mechanic", "") or "").strip()
    claim_support = (strat.get("claim_support", "") or "").strip()
    objective = (
        f'A {spec.get("goal", "clicks")}-focused static ad for "{aud.get("persona", "the target customer")}". '
        f'Express the "{strat.get("angle", "")}" angle. '
        f'Hook: "{hook}". Promise: "{promise}". '
        f"Render this strategy faithfully; do not change the marketing idea or add other claims."
    )
    if boldness:
        objective += f' Set the concept boldness to "{boldness}".'
    if disruption_target:
        objective += f" The concept attacks this target: {disruption_target}."
    if hook_tactic:
        objective += f' Use a "{hook_tactic}" hook tactic.'
    if trigger:
        objective += f' The emotional trigger is "{trigger}".'
    if mechanic:
        objective += f' Let the idea land through the "{mechanic}" mechanic.'
    if claim_support:
        objective += f" The claim is earned by this support: {claim_support}."
    sections["OBJECTIVE"] = objective

    # 2. PLATFORM & PLACEMENT
    sections["PLATFORM & PLACEMENT"] = (
        f'{spec.get("platform", "")} / {spec.get("placement", "")}. '
        f"Make it look native to that surface, not like a polished stock banner ad."
    )

    # 3. FORMAT
    ar = spec.get("aspect_ratio", "")
    sections["FORMAT"] = f"{ar} aspect ratio" + (f" ({size})." if size else ".")

    # 4. VISUAL STYLE
    sections["VISUAL STYLE"] = _s(vis.get("style", "") or "clean, native, on-brand")

    # 5. SCENE & SUBJECT — use the art-director core verbatim if present, else synthesize.
    authored = (pr.get("final_image_prompt") or "").strip()
    if authored:
        scene = _s(authored)
    else:
        scene = " ".join(_s(p) for p in [vis.get("scene", ""), vis.get("subject", "")] if p)
    focal = vis.get("focal_point", "")
    sections["SCENE & SUBJECT"] = (scene + (f" Focal point: {focal}." if focal else "")).strip()

    # 6. COMPOSITION + negative space + safe zones (don't repeat a space note the author gave)
    overlay_pos = lay.get("overlay_position", "bottom")
    comp = _s(vis.get("composition", "") or "clear, single-focal composition")
    if "negative space" not in comp.lower():
        comp += f" Reserve clean negative space in the {overlay_pos} for the overlay text."
    if lay.get("safe_zones"):
        comp += f" Keep key elements inside the safe zone: {lay['safe_zones']}."
    sections["COMPOSITION"] = comp

    # 7. LIGHTING & BACKGROUND (+ minimal props with explicit count)
    props = vis.get("props") or []
    lb = " ".join(_s(p) for p in [vis.get("lighting", ""), vis.get("background", "")] if p)
    if props:
        shown = props[:3]
        lb += f" Minimal props ({len(shown)}): {_join(shown)}."
    sections["LIGHTING & BACKGROUND"] = lb or "Soft, natural lighting; clean background."

    # 8. PRODUCT
    must_show = prod.get("must_show") or []
    pp = _s(prod.get("representation", "") or "product shown clearly")
    if must_show:
        pp += f" Must include: {_join(must_show)}."
    sections["PRODUCT"] = pp

    # 9. OVERLAY TEXT — exact words in quotes, or explicit "no text".
    overlay = (copy.get("overlay_text") or "").strip()
    if overlay:
        sections["OVERLAY TEXT"] = (
            f'Render exactly the words "{overlay}" in the {overlay_pos} of the frame. '
            f"Spell every word correctly. Keep it short and legible at thumbnail size."
        )
    else:
        sections["OVERLAY TEXT"] = "No baked-in text. Leave clean space for platform copy."

    # 10. TYPOGRAPHY & LAYOUT
    typ = _s(lay.get("typography", "") or "bold, high-contrast, legible sans-serif")
    logo = lay.get("logo_position", "none")
    sections["TYPOGRAPHY & LAYOUT"] = f"{typ} Overlay at {overlay_pos}. Logo: {logo}."

    # 11. BRAND CONSTRAINTS
    sections["BRAND CONSTRAINTS"] = (
        f"Stay on brand. {('Show: ' + _join(must_show) + '.') if must_show else 'Use the brand palette consistently.'}"
    )

    # 12. REALISM (fixed)
    sections["REALISM"] = _REALISM

    # 13. AVOID — union of must_not_show + negative_constraints.
    avoid = _join((prod.get("must_not_show") or []) + (pr.get("negative_constraints") or []))
    sections["AVOID"] = avoid or "fabricated third-party logos, invented endorsements, garbled text, clutter."

    # Assemble in fixed order.
    parts = ["ART-DIRECTION BRIEF — STATIC AD CREATIVE", ""]
    for label in SECTION_ORDER:
        parts.append(f"{label}: {sections[label]}".strip())
        parts.append("")

    if pr.get("reference_images"):
        parts.append(
            "REFERENCE IMAGES: brand/reference images are provided — match their product, "
            "logo, and palette exactly. Do not copy any third-party content from them."
        )
    return "\n".join(parts).rstrip() + "\n"


def _main(argv: List[str]) -> int:
    if not argv:
        print("usage: python compile_prompt.py <spec.json> [--size 1024x1280]", file=sys.stderr)
        return 2
    path = argv[0]
    size = None
    if "--size" in argv:
        size = argv[argv.index("--size") + 1]
    with open(path, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    print(compile_prompt(spec, size))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
