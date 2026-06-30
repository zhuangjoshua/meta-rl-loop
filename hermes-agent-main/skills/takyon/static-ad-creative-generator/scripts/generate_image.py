"""Generate one static ad creative from an ad spec — at one or many aspect ratios.

Pipeline: load spec -> validate (structure + lint) -> for each target ratio: compile prompt
-> resolve size -> call the image backend -> optional exact-ratio crop -> write the bundle.
Strategy is never invented here; it is read from the already-approved spec.

A creative can be rendered at several ratios at once (e.g. Meta wants 1:1, 9:16, and 1.91:1)
via --aspect-ratio. With no override, the spec's own aspect_ratio is used.

Bundle written next to the image(s):
    <creative_id>[__<ratio>].png         the image(s)
    <creative_id>[__<ratio>].prompt.txt  the compiled prompt per ratio
    <creative_id>.spec.json              the ad spec
    <creative_id>.qa.json                the QA report (pre-generation scaffold)
    <creative_id>.output.json            the delivery record (copy, paths, sizes, QA verdict)

CLI:
    python generate_image.py <spec.json> [-o output/] [--aspect-ratio 1:1,9:16,1.91:1]
                             [--crop] [--n 1] [--quality high]
                             [--backend openai] [--api-key-file PATH]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from backends import crop_to_aspect, get_backend, parse_ratio, read_api_key_file, resolve_size  # noqa: E402
from compile_prompt import compile_prompt  # noqa: E402
from qa_check import build_qa_report  # noqa: E402
from validate_spec import validate  # noqa: E402


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _ratio_key(ratio: str) -> str:
    """Filesystem-safe token for a ratio, e.g. '1.91:1' -> '1.91x1'."""
    return ratio.replace(":", "x")


def parse_aspect_ratios(value: Optional[str]) -> Optional[List[str]]:
    """Parse a comma-separated --aspect-ratio value into a validated list (or None)."""
    if not value:
        return None
    ratios = [r.strip() for r in value.split(",") if r.strip()]
    for r in ratios:
        parse_ratio(r)  # raises ValueError on a malformed ratio
    return ratios or None


def generate_one(
    spec: Dict,
    out_dir: str,
    backend=None,
    crop: bool = False,
    n: int = 1,
    quality: str = "high",
    strict: bool = False,
    aspect_ratios: Optional[List[str]] = None,
) -> Dict:
    """Generate, save the bundle, and return the delivery record. Raises on invalid spec."""
    cid = spec.get("creative_id") or "creative"
    errors, warnings = validate(spec)
    if errors:
        raise ValueError(f"[{cid}] spec failed validation:\n  - " + "\n  - ".join(errors))
    for _lvl, msg in warnings:
        print(f"  WARN [{cid}] {msg}", file=sys.stderr)
    if strict and warnings:
        raise ValueError(f"[{cid}] --strict: {len(warnings)} lint warning(s); fix before generating")

    os.makedirs(out_dir, exist_ok=True)
    backend = backend or get_backend()
    model = getattr(backend, "model", backend.name)
    model_str = model if isinstance(model, str) else "gpt-image-2"

    ratios = aspect_ratios or [spec["aspect_ratio"]]
    multi = len(ratios) > 1
    refs = spec.get("prompting", {}).get("reference_images") or []
    refs = [r for r in refs if os.path.exists(r)]

    # Compile prompts + resolve sizes inline (cheap), then dispatch the
    # independent, blocking backend.generate() calls in parallel.
    plans = []
    for ratio in ratios:
        render_spec = dict(spec)
        render_spec["aspect_ratio"] = ratio
        size = resolve_size(ratio, model_str)
        prompt = compile_prompt(render_spec, size)
        plans.append((ratio, size, prompt))

    def _render(plan):
        ratio, size, prompt = plan
        images = backend.generate(prompt=prompt, size=size, n=n, reference_images=refs or None, quality=quality)
        if crop:
            images = [crop_to_aspect(img, ratio) for img in images]
        return images

    if multi:
        with ThreadPoolExecutor(max_workers=len(plans)) as pool:
            generated = list(pool.map(_render, plans))
    else:
        generated = [_render(plan) for plan in plans]

    renders: List[Dict] = []
    for (ratio, size, prompt), images in zip(plans, generated):
        suffix = f"__{_ratio_key(ratio)}" if multi else ""
        prompt_name = f"{cid}{suffix}.prompt.txt"
        with open(os.path.join(out_dir, prompt_name), "w", encoding="utf-8") as fh:
            fh.write(prompt)

        image_names: List[str] = []
        for i, img in enumerate(images):
            idx = "" if len(images) == 1 else f"-{i + 1}"
            fn = f"{cid}{suffix}{idx}.png"
            with open(os.path.join(out_dir, fn), "wb") as fh:
                fh.write(img)
            image_names.append(fn)

        renders.append({"aspect_ratio": ratio, "size": size, "images": image_names, "prompt_file": prompt_name})
        print(f"  OK   [{cid}] {ratio:>7} -> {image_names[0]} ({size}, {backend.name})")

    # --- shared sidecars: spec, qa ---
    spec_path = os.path.join(out_dir, f"{cid}.spec.json")
    qa_path = os.path.join(out_dir, f"{cid}.qa.json")
    with open(spec_path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2)
    qa_report = build_qa_report(spec, stage="pre_generation")
    with open(qa_path, "w", encoding="utf-8") as fh:
        json.dump(qa_report, fh, indent=2)

    # --- delivery record (Output, item 7) ---
    copy = spec.get("copy", {})
    qa = spec.get("qa", {})
    record = {
        "creative_id": cid,
        "generated_at": _now(),
        "backend": backend.name,
        "model": model_str if backend.name == "openai" else backend.name,
        "platform": spec.get("platform"),
        "placement": spec.get("placement"),
        "angle": spec.get("strategy", {}).get("angle"),
        "aspect_ratios": ratios,
        "renders": renders,
        "spec_file": os.path.basename(spec_path),
        "qa_file": os.path.basename(qa_path),
        "suggested_headline": copy.get("headline"),
        "suggested_primary_text": copy.get("primary_text"),
        "cta": copy.get("cta"),
        "qa_notes": {
            "readability_check": qa.get("readability_check"),
            "native_platform_check": qa.get("native_platform_check"),
            "policy_risks": qa.get("policy_risks", []),
            "verdict": qa_report["verdict"],
            "policy_gate": qa_report["policy_gate"],
            "lint_warnings": [m for _l, m in warnings],
        },
        "recommended_next_iteration": qa.get("iteration_notes", ""),
    }
    out_path = os.path.join(out_dir, f"{cid}.output.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    return record


def _main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Generate one static ad creative from an ad spec, at one or many ratios.")
    ap.add_argument("spec", help="path to a single ad-spec JSON file")
    ap.add_argument("-o", "--out", default="output", help="output directory (default: output/)")
    ap.add_argument("--aspect-ratio", default=None,
                    help="override the spec ratio; comma-separated for multi-size, e.g. 1:1,9:16,1.91:1")
    ap.add_argument("--crop", action="store_true", help="center-crop to the exact aspect ratio (needs Pillow)")
    ap.add_argument("--n", type=int, default=1, help="images to generate per ratio")
    ap.add_argument("--quality", default="high", choices=["low", "medium", "high", "auto"])
    ap.add_argument("--backend", default=None, help="backend name (default: openai)")
    ap.add_argument("--api-key-file", default=None,
                    help="read the API key from this file and pass it straight to the client (never sets an env var)")
    ap.add_argument("--strict", action="store_true", help="treat lint warnings as errors")
    args = ap.parse_args(argv)

    with open(args.spec, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    backend = get_backend(name=args.backend, api_key=read_api_key_file(args.api_key_file))
    generate_one(spec, args.out, backend=backend, crop=args.crop, n=args.n,
                 quality=args.quality, strict=args.strict, aspect_ratios=parse_aspect_ratios(args.aspect_ratio))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
