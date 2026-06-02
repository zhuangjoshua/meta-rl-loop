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

import json
import os
import re
import sys
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "..", "templates", "ad-spec.schema.json")

VALID_PLACEMENTS = {
    "meta": {"feed", "story", "reels"},
}

# Common aspect ratios per placement (see references/platform-specs.md). Any W:H is allowed;
# this only drives a soft warning when a ratio is unusual for the chosen placement.
RECOMMENDED_RATIOS = {
    "feed": {"1:1", "4:5", "1.91:1"},
    "story": {"9:16"},
    "reels": {"9:16"},
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


# ----------------------------- spec loading -----------------------------
def load_specs(path: str) -> List[Dict]:
    """Load one spec, an array of specs, or a directory of *.json into a list of dicts."""
    if os.path.isdir(path):
        specs: List[Dict] = []
        for fn in sorted(os.listdir(path)):
            if fn.endswith(".json") and not fn.endswith(".schema.json"):
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
    rec = RECOMMENDED_RATIOS.get(placement)
    if rec and ar not in rec:
        out.append(("warn", f"aspect_ratio '{ar}' is unusual for placement '{placement}'; common: {', '.join(sorted(rec))}"))
    try:  # gpt-image-2 supports 1:3..3:1; flag out-of-range early (schema only checks the W:H shape)
        _w, _h = (float(x) for x in str(ar).split(":"))
        if _w > 0 and _h > 0 and not (1 / 3) <= _w / _h <= 3:
            out.append(("warn", f"aspect_ratio '{ar}' (~{_w / _h:.2f}) is outside the 1:3..3:1 range; generation will reject it"))
    except Exception:
        pass  # malformed strings are caught by the schema pattern

    overlay = (copy.get("overlay_text") or "")
    if len(overlay) > 50:
        out.append(("warn", f"overlay_text is {len(overlay)} chars; keep <=50 for in-feed readability"))
    if len(copy.get("headline") or "") > 40:
        out.append(("warn", "headline >40 chars may truncate on Meta"))
    if len(copy.get("primary_text") or "") > 125:
        out.append(("warn", "primary_text >125 chars truncates before '...more' on mobile"))

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
    angle = (strat.get("angle") or "").lower()
    risks_text = " ".join(qa.get("policy_risks") or []).lower()
    if angle in PROOF_ANGLES and not any(w in risks_text for w in PROOF_OK_WORDS):
        out.append((
            "warn",
            f"angle '{angle}' relies on proof/social signals; qa.policy_risks must state whether "
            "it is real & rights-cleared or labeled illustrative/representative",
        ))

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
