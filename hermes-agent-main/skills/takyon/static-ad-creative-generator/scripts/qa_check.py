"""Scaffold a QA report (the 9 checks in references/qa-rubric.md) from an ad spec.

Automatable checks (overlay length, policy lint, platform consistency) get a computed verdict.
Subjective/visual checks get ``review`` for an agent or human to finalize — especially after
the image exists (``--stage post_generation``). Check 6 (policy) is a hard gate.

CLI:
    python qa_check.py <spec.json> [--stage pre_generation|post_generation] [-o out.json]
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from validate_spec import lint_spec  # noqa: E402

# Lint messages that represent POLICY (not formatting) concerns -> feed the policy gate.
_POLICY_MARKERS = (
    "personal-attribute", "protected", "guarantee", "authority", "weight-loss",
    "proof/social", "third-party", "misleading", "non-functional",
)

_CHECKS = [
    (1, "hook_readable", "Is the hook readable in feed at thumbnail size?"),
    (2, "communicates_in_2s", "Does the image communicate the product/category in under 2s?"),
    (3, "native_to_platform", "Is the visual native to the platform (not a stock banner)?"),
    (4, "overlay_short", "Is the overlay text short enough?"),
    (5, "not_generic", "Does it avoid looking too corporate/generic?"),
    (6, "policy_clear", "No fake claims / personal-attribute / misleading-UI / invented-proof?"),
    (7, "click_worthy", "Would this plausibly get clicked by the target persona?"),
    (8, "matches_angle", "Does it match the chosen angle?"),
    (9, "matches_spec", "Does it match the ad spec (visual, overlay text, layout, product)?"),
]


def build_qa_report(spec: Dict, stage: str = "pre_generation") -> Dict:
    warnings = lint_spec(spec)
    msgs = [m for _lvl, m in warnings]
    policy_msgs = [m for m in msgs if any(k in m for k in _POLICY_MARKERS)]
    overlay = (spec.get("copy", {}).get("overlay_text") or "")

    results: List[Dict] = []
    for cid, name, _q in _CHECKS:
        if name == "overlay_short":
            ok = 0 < len(overlay) <= 50
            results.append({"id": cid, "name": name, "result": "pass" if ok else ("warn" if overlay else "review"),
                            "note": f"{len(overlay)} chars" if overlay else "no overlay text set"})
        elif name == "policy_clear":
            results.append({"id": cid, "name": name,
                            "result": "warn" if policy_msgs else "pass",
                            "note": "; ".join(policy_msgs) or "no policy lint flags (confirm visually)"})
        else:
            results.append({"id": cid, "name": name, "result": "review",
                            "note": "needs visual confirmation" if stage == "post_generation" else "decide from spec/brief"})

    policy_gate = "review" if policy_msgs else "clear"
    if any(r["result"] == "fail" for r in results):
        verdict = "block"
    elif any(r["result"] in ("warn", "review") for r in results):
        verdict = "iterate"
    else:
        verdict = "ship"

    return {
        "creative_id": spec.get("creative_id"),
        "stage": stage,
        "checks": results,
        "policy_gate": policy_gate,
        "verdict": verdict,
        "recommended_next_iteration": spec.get("qa", {}).get("iteration_notes", ""),
        "format_warnings": [m for m in msgs if m not in policy_msgs],
    }


def _main(argv: List[str]) -> int:
    if not argv:
        print("usage: python qa_check.py <spec.json> [--stage ...] [-o out.json]", file=sys.stderr)
        return 2
    path = argv[0]
    stage = argv[argv.index("--stage") + 1] if "--stage" in argv else "pre_generation"
    out_path = argv[argv.index("-o") + 1] if "-o" in argv else None
    with open(path, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    report = build_qa_report(spec, stage)
    text = json.dumps(report, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {out_path} (verdict: {report['verdict']}, policy: {report['policy_gate']})")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
