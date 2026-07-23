"""Adapt the Tier-B semantic-gradient loop to the population-market v2 engine.

Pure translation, no computation: the loop's per-iteration three-ad spec is
converted into a population-market v2 policy, evaluated through the untouched
`population_market_v2.evaluate_policies`, and returned to the loop in the
legacy receipt shape it already consumes (rows / funnel / spend / revenue /
roas). Sampled receipts come from the engine's own `sampled_replay`; expected
receipts map first-payment revenue/ROAS onto the legacy field names.

The engine is stateless between iterations: every iteration's audience starts
fresh (no cross-iteration frequency, fatigue, or purchase depletion). Receipts
carry `periods_independent: true` so downstream readers cannot mistake the
cumulative numbers for a persistent-audience simulation.

v2 budget semantics are preserved, not hidden: a campaign budget is per
delivery period, and one iteration's spend is budget x periods.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

try:  # package import in tests; direct import when run as `python sim/...py`
    from .population_market_v2 import (
        CHOICE_CALIBRATION_PATH,
        MODEL_VERSION,
        PopulationMarketError,
        evaluate_policies,
        load_variation_model,
        sampled_replay,
        validate_policy,
    )
    from .tier_b_market import TierBError, validate_spec
except ImportError:  # pragma: no cover - direct-script path
    from population_market_v2 import (
        CHOICE_CALIBRATION_PATH,
        MODEL_VERSION,
        PopulationMarketError,
        evaluate_policies,
        load_variation_model,
        sampled_replay,
        validate_policy,
    )
    from tier_b_market import TierBError, validate_spec


SYNTHESIZED_TIMELINE_SECONDS = 15.0


class MarketV2AdapterError(RuntimeError):
    pass


JUDGE_RETRY_ATTEMPTS = 3


def _microprofile_rows_invalid(payload: Any, allowed_ids: set[str]) -> bool:
    """Mirror of the engine's fail-closed microprofile rule, applied to a raw
    cached judge payload: helped/rejected must be duplicate-free, disjoint,
    and drawn from the known profile ids."""
    if not isinstance(payload, Mapping):
        return False
    rows = payload.get("ad_assessments")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        helped = [str(value) for value in row.get("helped_microprofiles") or []]
        rejected = [str(value) for value in row.get("rejected_microprofiles") or []]
        if (
            len(helped) != len(set(helped)) or len(rejected) != len(set(rejected))
            or not set(helped + rejected) <= allowed_ids or set(helped) & set(rejected)
        ):
            return True
    return False


def _purge_invalid_judge_cache(cache_dir: Path, allowed_ids: set[str]) -> list[str]:
    """Delete cached judge responses that provably violate the engine's
    microprofile rule so a retry resamples them instead of replaying the same
    rejected payload. Only provably-invalid entries are removed; valid cached
    judgments are untouched."""
    removed: list[str] = []
    for path in sorted(cache_dir.glob(f"{MODEL_VERSION}-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload = data.get("value") if isinstance(data, Mapping) else None
        if payload is None:
            payload = data
        if _microprofile_rows_invalid(payload, allowed_ids):
            path.unlink()
            removed.append(path.name)
    return removed


def _ensure_timeline(ad: dict[str, Any]) -> dict[str, Any]:
    """Attach a deterministic single-scene static-ad timeline when the design
    carries none. The synthesized scene exposes only the ad's own visual and
    headline, and the ad is marked so receipts and archived specs show which
    timelines were authored versus synthesized."""
    if ad.get("duration_seconds") is not None and ad.get("scenes"):
        return ad
    visual = str(ad.get("visual") or "").strip()
    headline = str(ad.get("headline") or "").strip()
    content = (
        f"Static image ad shown for the full view. Visual: {visual or 'plain product image'}. "
        f"Headline text overlay: {headline or 'untitled'}."
    )
    return {
        **ad,
        "duration_seconds": SYNTHESIZED_TIMELINE_SECONDS,
        "scenes": [
            {
                "start_second": 0.0,
                "end_second": SYNTHESIZED_TIMELINE_SECONDS,
                "content": content,
            }
        ],
        "timeline_synthesized": True,
    }


def _policy_from_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    ads = []
    for ad in spec["ads"]:
        ads.append(
            {
                "id": ad["id"],
                "headline": ad["headline"],
                "primary_text": str(ad.get("primary_text") or ad.get("message") or ""),
                "call_to_action": ad["call_to_action"],
                "duration_seconds": ad["duration_seconds"],
                "scenes": ad["scenes"],
            }
        )
    return {
        "id": f"it{spec['iteration']}-{spec['policy']}",
        "landing_page": spec["landing_page"],
        "ads": ads,
        "campaigns": [dict(campaign) for campaign in spec["campaigns"]],
    }


class MarketV2Backend:
    """The three market operations the loop needs, backed by population-market
    v2 with a frozen generated world. Never touches the engine's internals."""

    name = "v2"

    def __init__(
        self,
        *,
        world_number: int,
        judge: Any,
        budget: float,
        periods: int,
        concurrency: int,
        model_path: Path,
        variation_path: Path | None = None,
        economics_path: Path | None = None,
        choice_calibration_path: Path | None = None,
    ) -> None:
        if model_path is None:
            raise MarketV2AdapterError("market v2 requires an explicit population model path")
        self.world_number = world_number
        self.judge = judge
        self.budget = budget
        self.periods = periods
        self.concurrency = concurrency
        self.model_path = Path(model_path)
        self.variation_path = Path(variation_path) if variation_path else None
        self.economics_path = Path(economics_path) if economics_path else None
        self.choice_calibration_path = (
            Path(choice_calibration_path) if choice_calibration_path else CHOICE_CALIBRATION_PATH
        )
        variation = load_variation_model(self.variation_path)
        self._allowed_profile_ids = (
            {str(profile["id"]) for profile in variation["human_microprofiles"]}
            if variation
            else {"general_human"}
        )

    @property
    def design_instructions(self) -> str:
        return (
            f"This batch runs unchanged for {self.periods} delivery periods; campaign budgets "
            "are per period. Each ad must also include duration_seconds (3-120) and a scenes "
            "timeline (objects with start_second, end_second, content) covering the ad "
            "continuously from zero to its duration; each scene's content states concretely "
            "what is visible or audible in that span. A static image ad may use one scene "
            "covering the full duration. Consumers respond only to content plausibly seen "
            "before they abandon the ad."
        )

    def validate(self, spec: Mapping[str, Any], platform: Mapping[str, Any]) -> dict[str, Any]:
        """Fail-fast validation for the design/seed step: legacy spec semantics
        first (unchanged campaign rules), then the v2 policy contract, so an
        invalid design is rejected with the exact engine error while the loop
        can still retry with feedback."""
        try:
            validated = validate_spec(spec, platform)
        except TierBError as exc:
            raise MarketV2AdapterError(str(exc)) from exc
        validated["ads"] = [_ensure_timeline(dict(ad)) for ad in validated["ads"]]
        try:
            validate_policy(
                _policy_from_spec(validated), platform, expected_budget=self.budget
            )
        except PopulationMarketError as exc:
            raise MarketV2AdapterError(f"population-market v2 rejected the design: {exc}") from exc
        return validated

    def simulate(
        self, *, seed: int, raw_spec: Mapping[str, Any], expected: bool = False
    ) -> dict[str, Any]:
        result = None
        for attempt in range(1, JUDGE_RETRY_ATTEMPTS + 1):
            try:
                result = evaluate_policies(
                    world_number=self.world_number,
                    raw_policies=[_policy_from_spec(raw_spec)],
                    judge=self.judge,
                    periods=self.periods,
                    concurrency=self.concurrency,
                    expected_budget=self.budget,
                    model_path=self.model_path,
                    variation_path=self.variation_path,
                    economics_path=self.economics_path,
                    choice_calibration_path=self.choice_calibration_path,
                )[0]
                break
            except PopulationMarketError as exc:
                # A semantically invalid judge response is cached before the
                # engine validates it, so a bare retry would replay the same
                # rejected payload forever. Purge provably-invalid cached
                # judgments and resample; anything else fails immediately.
                cache_dir = getattr(self.judge, "response_cache_dir", None)
                removed = (
                    _purge_invalid_judge_cache(Path(cache_dir), self._allowed_profile_ids)
                    if cache_dir is not None
                    else []
                )
                if not removed or attempt == JUDGE_RETRY_ATTEMPTS:
                    raise MarketV2AdapterError(
                        f"population-market v2 evaluation failed: {exc}"
                    ) from exc
        if expected:
            receipt = {
                "schema": "takyon.population-market-receipt.v3-expected",
                "world": result["world"],
                "periods": result["periods"],
                "rows": [dict(row) for row in result["rows"]],
                "funnel": dict(result["funnel"]),
                "spend": result["spend"],
                "revenue": result["first_payment_revenue"],
                "roas": result["first_payment_roas"],
            }
        else:
            receipt = sampled_replay(result, seed=seed)
        receipt["periods_independent"] = True
        receipt["revenue_definition"] = "first_payment"
        return receipt
