"""Validate ad-creative specs: JSON-Schema structure + performance/policy lint.

Two layers:
  1. STRUCTURE — validate against templates/ad-spec.schema.json. Uses ``jsonschema`` if
     installed, otherwise a built-in zero-dependency fallback (types/required/enum/pattern).
  2. LINT — performance + policy heuristics from references/platform-specs.md and
     references/policy-checks.md. These are WARNINGS; an agent/human still confirms.

CLI:
    python validate_spec.py <spec.json | batch.json | dir/>   # exits non-zero on errors
"""

from __future__ import annotations

import functools
import json
import os
import re
import sys
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "..", "templates", "ad-spec.schema.json")

VALID_PLACEMENTS = {
    "meta": {"feed", "story", "reels"},
    "reddit": {"feed"},
}

# Common aspect ratios per placement (see references/platform-specs.md). Any W:H is allowed;
# this only drives a soft warning when a ratio is unusual for the chosen placement.
RECOMMENDED_RATIOS = {
    ("meta", "feed"): {"1:1", "4:5", "1.91:1"},
    ("meta", "story"): {"9:16"},
    ("meta", "reels"): {"9:16"},
    ("reddit", "feed"): {"1:1", "4:5", "1.91:1"},
}

# --- policy lint vocabularies (keep in sync with references/policy-checks.md) ---
PROTECTED_CONDITIONS = [
    "depressed", "depression", "anxiety", "anxious", "diabetes", "diabetic", "hiv",
    "cancer", "bankrupt", "in debt", "overweight", "obese", "addiction", "pregnant",
]
GUARANTEE_TERMS = [
    "guaranteed", "100%", "cure", "instantly", "risk-free", "riskfree", "miracle",
    "no risk", "permanent results",
]
AUTHORITY_TERMS = ["as seen in", "#1 doctor", "number one doctor", "clinically proven", "fda approved"]
PROOF_ANGLES = {"social_proof", "community_native", "imessage", "fake_ui", "testimonial"}
PROOF_OK_WORDS = ["real", "rights-cleared", "rights cleared", "illustrative", "representative", "sample", "fictional"]
UI_CONTROL_TERMS = ["play button", "cursor", "notification", "close button", "progress bar"]
PROVOCATIVE_TACTICS = {"contrarian", "warning", "confession", "curiosity", "shocking_statement"}
HIGH_VOLTAGE_TACTICS = {"contrarian", "warning", "shocking_statement", "comparison", "price_anchor"}
UI_TEMPLATES = {"ui_screenshot", "device_in_hand"}
COLD_AWARENESS = {"unaware", "problem_aware"}
FEATURE_HEAVY_TERMS = [
    "dashboard", "workspace", "editor", "preview", "comments", "citation", "screen",
    "platform", "feature", "ui", "workflow", "source",
]
OUTCOME_SIGNALS = [
    "from", "to", "stop", "start", "without", "faster", "calm", "clear", "confident",
    "submitted", "submit-ready", "ready", "stronger", "better", "more", "less", "finish",
]
SOFTENING_TERMS = ["easier", "simple", "simply", "help", "helps", "organized", "organize", "streamline", "modern", "seamless"]
BOLDNESS_MARKERS = ["stop", "don't", "never", "wrong", "waste", "broken", "cost", "chaos", "vs", "from", "kill", "stuck"]
GENERIC_HOOK_PHRASES = [
    "all-in-one",
    "all in one",
    "better way",
    "modern teams",
    "powerful platform",
    "everything you need",
    "seamless",
    "innovative",
    "streamline",
    "work smarter",
]
TACTIC_MARKERS = {
    "contrarian": ["don't", "stop", "wrong", "still", "actually", "isn't", "doesn't", "instead", "myth"],
    "warning": ["don't", "before", "avoid", "stop", "never"],
    "confession": ["i ", "we ", "i'm", "we're", "i thought", "we thought", "i was wrong", "we messed up"],
    "curiosity": ["why", "how", "what", "until", "secret", "mistake", "still"],
    "shocking_statement": ["wrong", "actually", "never", "isn't", "doesn't", "still", "why"],
}


# ----------------------------- spec loading -----------------------------
def load_specs(path: str) -> List[Dict]:
    """Load one spec, an array of specs, or a directory of *.json into a list of dicts."""
    if os.path.isdir(path):
        specs: List[Dict] = []
        files = sorted(os.listdir(path))
        preferred = [fn for fn in files if fn.endswith(".spec.json")]
        if preferred:
            candidates = preferred
        else:
            candidates = [
                fn for fn in files
                if fn.endswith(".json")
                and not fn.endswith((".schema.json", ".output.json", ".qa.json"))
                and fn != "manifest.json"
            ]
        for fn in candidates:
            specs.extend(load_specs(os.path.join(path, fn)))
        return specs
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("creatives"), list):
        return data["creatives"]
    return [data]


# ----------------------------- structure validation -----------------------------
@functools.lru_cache(maxsize=1)
def _load_schema() -> Dict:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def validate_structure(spec: Dict) -> List[str]:
    """Return a list of structural errors ([] = valid)."""
    schema = _load_schema()
    try:
        import jsonschema  # type: ignore

        validator = jsonschema.Draft202012Validator(schema)
        return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in validator.iter_errors(spec)]
    except Exception:
        return _fallback_validate(spec, schema, "")


_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def _fallback_validate(value, schema: Dict, path: str) -> List[str]:
    """Minimal Draft-2020 subset: type, required, enum, properties, items, pattern, minLength."""
    errors: List[str] = []
    loc = path or "<root>"

    t = schema.get("type")
    if t and not _TYPE_CHECKS.get(t, lambda _v: True)(value):
        errors.append(f"{loc}: expected type {t}")
        return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{loc}: {value!r} not in {schema['enum']}")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{loc}: {value!r} does not match pattern {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{loc}: shorter than minLength {schema['minLength']}")

    if isinstance(value, dict) and schema.get("type") == "object":
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{loc}: missing required field '{req}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"{loc}: unexpected field '{key}'")
        for key, subschema in props.items():
            if key in value:
                errors.extend(_fallback_validate(value[key], subschema, f"{path}.{key}" if path else key))

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors.extend(_fallback_validate(item, schema["items"], f"{loc}[{i}]"))
    return errors


# ----------------------------- lint -----------------------------
def lint_spec(spec: Dict) -> List[Tuple[str, str]]:
    """Return [(level, message)] where level is 'warn'. Structural fields assumed present."""
    out: List[Tuple[str, str]] = []

    platform = spec.get("platform")
    placement = spec.get("placement")
    ar = spec.get("aspect_ratio")
    copy = spec.get("copy", {})
    strat = spec.get("strategy", {})
    prod = spec.get("product", {})
    qa = spec.get("qa", {})
    pr = spec.get("prompting", {})

    # --- consistency (platform-specs.md) ---
    if platform in VALID_PLACEMENTS and placement not in VALID_PLACEMENTS[platform]:
        out.append(("warn", f"placement '{placement}' is not valid for platform '{platform}'"))
    rec = RECOMMENDED_RATIOS.get((platform, placement))
    if rec and ar not in rec:
        out.append(("warn", f"aspect_ratio '{ar}' is unusual for placement '{placement}'; common: {', '.join(sorted(rec))}"))
    try:  # gpt-image-2 supports 1:3..3:1; flag out-of-range early (schema only checks the W:H shape)
        _w, _h = (float(x) for x in str(ar).split(":"))
        if _w > 0 and _h > 0 and not (1 / 3) <= _w / _h <= 3:
            out.append(("warn", f"aspect_ratio '{ar}' (~{_w / _h:.2f}) is outside the 1:3..3:1 range; generation will reject it"))
    except Exception:
        pass  # malformed strings are caught by the schema pattern

    overlay = (copy.get("overlay_text") or "")
    hook = str(strat.get("hook") or "").strip()
    boldness = str(strat.get("boldness") or "").strip().lower()
    disruption_target = str(strat.get("disruption_target") or "").strip()
    hook_tactic = str(strat.get("hook_tactic") or "").strip().lower()
    claim_support = str(strat.get("claim_support") or "").strip()
    creative_mechanic = str(strat.get("creative_mechanic") or "").strip().lower()
    awareness = str((spec.get("audience") or {}).get("awareness_level") or "").strip().lower()
    template = str((spec.get("visual") or {}).get("template") or "").strip().lower()
    representation = str((prod.get("representation") or "")).strip().lower()
    if len(overlay) > 50:
        out.append(("warn", f"overlay_text is {len(overlay)} chars; keep <=50 for in-feed readability"))
    if len(copy.get("headline") or "") > 40:
        out.append(("warn", "headline >40 chars may truncate on Meta"))
    if len(copy.get("primary_text") or "") > 125:
        out.append(("warn", "primary_text >125 chars truncates before '...more' on mobile"))
    if hook:
        hook_words = len(re.findall(r"\b\w+\b", hook))
        if hook_words > 12:
            out.append(("warn", "strategy.hook is long; punchier 3-10 word hooks usually stop scroll better"))
        if any(p in hook.lower() for p in GENERIC_HOOK_PHRASES):
            out.append(("warn", "strategy.hook sounds like generic category copy; make it more belief-breaking, specific, or proof-led"))
        if any(term in hook.lower() for term in FEATURE_HEAVY_TERMS) and not any(sig in hook.lower() for sig in OUTCOME_SIGNALS):
            out.append(("warn", "strategy.hook may be feature-led rather than outcome-led; colder ads usually sell transformation, relief, or identity first"))
        if boldness == "hard" and not any(marker in hook.lower() for marker in BOLDNESS_MARKERS):
            out.append(("warn", "strategy.boldness='hard' but the hook text may still read too polite; make the line more blunt, contrastive, or interruptive"))
        if boldness in {"medium", "hard"} and any(term in hook.lower() for term in SOFTENING_TERMS):
            out.append(("warn", "the hook uses softening language that may dilute the punch; consider a sharper statement or warning"))
    if not boldness:
        out.append(("warn", "strategy.boldness missing; choose soft, medium, or hard intentionally"))
    if not hook_tactic:
        out.append(("warn", "strategy.hook_tactic missing; choose a deliberate tactic (contrarian, warning, confession, curiosity, statistic, etc.) from references/hook-strategy.md"))
    elif hook and not any(marker in hook.lower() for marker in TACTIC_MARKERS.get(hook_tactic, []) or []):
        if hook_tactic in TACTIC_MARKERS:
            out.append(("warn", f"strategy.hook_tactic='{hook_tactic}' but the hook text may not express that tactic strongly"))
    if not claim_support:
        out.append(("warn", "strategy.claim_support missing; name the concrete proof that makes the hook fair"))
    elif len(claim_support) < 18:
        out.append(("warn", "strategy.claim_support is terse; name the actual demo, stat, screenshot, quote, or comparison"))
    if boldness == "hard" and not disruption_target:
        out.append(("warn", "strategy.boldness='hard' needs a disruption_target; name the belief, behavior, or old way the ad is attacking"))
    if boldness == "hard" and hook_tactic and hook_tactic not in HIGH_VOLTAGE_TACTICS:
        out.append(("warn", f"strategy.boldness='hard' usually wants a higher-voltage tactic than '{hook_tactic}'"))
    if hook_tactic in PROVOCATIVE_TACTICS and not creative_mechanic:
        out.append(("warn", f"strategy.hook_tactic='{hook_tactic}' is provocative; add strategy.creative_mechanic so the concept is reproducible"))
    if awareness in COLD_AWARENESS and template in UI_TEMPLATES:
        out.append(("warn", "cold-audience creative uses a UI-led template; test an outcome, transformation, or before/after concept first"))
    if awareness in COLD_AWARENESS and "on-screen ui" in representation:
        out.append(("warn", "cold-audience creative shows the interface as the main product representation; this may explain the feature instead of selling the after-state"))
    if awareness in COLD_AWARENESS and boldness == "soft":
        out.append(("warn", "cold-audience creative is marked soft; consider at least one medium or hard variant for the first test wave"))
    angle = (strat.get("angle") or "").lower()
    if angle in {"transformation", "before_after", "pain_agitation"} and ("on-screen ui" in representation or template in UI_TEMPLATES):
        out.append(("warn", f"angle '{angle}' usually works better when the outcome or contrast is visible without relying on the product UI"))

    # --- policy lint (policy-checks.md, Section: machine-checkable signals) ---
    text = f"{overlay} {copy.get('primary_text','')} {copy.get('headline','')}".lower()
    if re.search(r"\bare you\b[^.?!]*\?", text) or "do you suffer" in text or "struggling with" in text:
        out.append(("warn", "possible personal-attribute targeting; frame the situation/desire, not the person"))
    for cond in PROTECTED_CONDITIONS:
        if re.search(rf"\b{re.escape(cond)}\b", text):
            out.append(("warn", f"references a sensitive/protected condition ('{cond}'); review personal-attribute policy"))
            break
    if re.search(r"lose \d+\s*(lbs|pounds|kg)", text):
        out.append(("warn", "specific weight-loss claim; restricted on Meta, verify substantiation"))
    for term in GUARANTEE_TERMS:
        if term in text:
            out.append(("warn", f"absolute/guarantee language ('{term}'); avoid unrealistic outcome claims"))
            break
    for term in AUTHORITY_TERMS:
        if term in text:
            out.append(("warn", f"borrowed-authority phrase ('{term}'); needs a real, cited source"))
            break

    # proof-bearing angle must declare real vs labeled
    risks_text = " ".join(qa.get("policy_risks") or []).lower()
    if angle in PROOF_ANGLES and not any(w in risks_text for w in PROOF_OK_WORDS):
        out.append((
            "warn",
            f"angle '{angle}' relies on proof/social signals; qa.policy_risks must state whether "
            "it is real & rights-cleared or labeled illustrative/representative",
        ))
    if angle == "contrarian" and hook_tactic and hook_tactic not in {"contrarian", "warning", "shocking_statement", "question"}:
        out.append(("warn", "angle 'contrarian' usually pairs best with a belief-breaking hook tactic such as contrarian, warning, shocking_statement, or question"))

    # competitor / IP hygiene
    mns = " ".join(prod.get("must_not_show") or []).lower()
    if not any(k in mns for k in ["logo", "endorsement", "third-party", "third party"]):
        out.append(("warn", "product.must_not_show should exclude fabricated third-party logos/endorsements"))

    # misleading UI controls
    fip = (pr.get("final_image_prompt") or "").lower()
    for term in UI_CONTROL_TERMS:
        if term in fip:
            out.append(("warn", f"final_image_prompt mentions '{term}'; avoid non-functional/misleading UI"))
            break

    return out


def validate(spec: Dict) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Return (errors, warnings) for one spec."""
    errors = validate_structure(spec)
    warnings = [] if errors else lint_spec(spec)
    return errors, warnings


# ----------------------------- CLI -----------------------------
def _main(argv: List[str]) -> int:
    if not argv:
        print("usage: python validate_spec.py <spec.json | batch.json | dir/>", file=sys.stderr)
        return 2
    specs = load_specs(argv[0])
    total_errors = 0
    for i, spec in enumerate(specs):
        cid = spec.get("creative_id", f"#{i}")
        errors, warnings = validate(spec)
        if errors:
            total_errors += len(errors)
            print(f"\n[FAIL] {cid}")
            for e in errors:
                print(f"  ERROR  {e}")
        else:
            print(f"\n[OK]   {cid}")
        for level, msg in warnings:
            print(f"  WARN   {msg}")
    print(f"\n{len(specs)} spec(s) checked, {total_errors} error(s).")
    return 1 if total_errors else 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
