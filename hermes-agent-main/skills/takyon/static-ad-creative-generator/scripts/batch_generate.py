"""Batch-generate static ad creatives from many specs.

Input is one of:
  - a JSON array of ad specs,
  - an object with a "creatives": [...] array,
  - a directory of *.spec.json / *.json files.

Each spec runs through the same single-creative pipeline (validate -> compile -> generate ->
QA -> bundle). A manifest.json summarizing every creative is written to the output dir.

CLI:
    python batch_generate.py <batch.json | dir/> [-o output/] [--crop]
                            [--max N] [--quality high] [--backend openai] [--strict]
                            [--stop-on-error]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from generate_image import generate_one, parse_aspect_ratios  # noqa: E402
from validate_spec import load_specs  # noqa: E402
from backends import get_backend, read_api_key_file  # noqa: E402


def _main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Batch-generate static ad creatives from specs.")
    ap.add_argument("input", help="batch JSON (array or {creatives:[...]}) or a directory of specs")
    ap.add_argument("-o", "--out", default="output", help="output directory (default: output/)")
    ap.add_argument("--crop", action="store_true", help="center-crop to exact aspect ratio (needs Pillow)")
    ap.add_argument("--max", type=int, default=None, help="cap the number of creatives generated")
    ap.add_argument("--quality", default="high", choices=["low", "medium", "high", "auto"])
    ap.add_argument("--backend", default=None, help="backend name (default: openai)")
    ap.add_argument("--api-key-file", default=None,
                    help="read the API key from this file and pass it straight to the client (never sets an env var)")
    ap.add_argument("--aspect-ratio", default=None,
                    help="override every spec's ratio; comma-separated for multi-size, e.g. 1:1,9:16,1.91:1")
    ap.add_argument("--strict", action="store_true", help="treat lint warnings as errors")
    ap.add_argument("--stop-on-error", action="store_true", help="abort on the first failure")
    args = ap.parse_args(argv)
    aspect_ratios = parse_aspect_ratios(args.aspect_ratio)

    specs = load_specs(args.input)
    if args.max is not None:
        if len(specs) > args.max:
            print(f"NOTE: capping at --max {args.max} of {len(specs)} specs.", file=sys.stderr)
        specs = specs[: args.max]
    os.makedirs(args.out, exist_ok=True)
    backend = get_backend(name=args.backend,
                          api_key=read_api_key_file(args.api_key_file))

    print(f"Generating {len(specs)} creative(s) -> {args.out} (backend={backend.name})")
    records, failures = [], []
    for spec in specs:
        cid = spec.get("creative_id", "?")
        try:
            records.append(generate_one(
                spec, args.out, backend=backend, crop=args.crop,
                quality=args.quality, strict=args.strict, aspect_ratios=aspect_ratios,
            ))
        except Exception as exc:  # keep going by default so one bad spec doesn't sink the batch
            failures.append({"creative_id": cid, "error": str(exc)})
            print(f"  FAIL [{cid}] {exc}", file=sys.stderr)
            if args.stop_on_error:
                break

    manifest = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "backend": backend.name,
        "requested": len(specs),
        "succeeded": len(records),
        "failed": len(failures),
        "creatives": records,
        "failures": failures,
    }
    manifest_path = os.path.join(args.out, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"\nDone: {len(records)} ok, {len(failures)} failed. Manifest: {manifest_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
