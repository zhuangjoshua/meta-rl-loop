"""Search complete three-ad policies against the revealed population-market v2."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .llm_client import LLMConfig, StructuredLLM
    from .population_market_v2 import (
        CHOICE_CALIBRATION_PATH,
        MODEL_PATH,
        SIM_ROOT,
        evaluate_policies,
        load_population_model,
        load_variation_model,
        public_receipt,
        sampled_replay,
    )
    from .tier_b_market import _load_world
except ImportError:  # pragma: no cover
    from llm_client import LLMConfig, StructuredLLM
    from population_market_v2 import (
        CHOICE_CALIBRATION_PATH,
        MODEL_PATH,
        SIM_ROOT,
        evaluate_policies,
        load_population_model,
        load_variation_model,
        public_receipt,
        sampled_replay,
    )
    from tier_b_market import _load_world


class FullInfoPolicyError(RuntimeError):
    pass


AUDIENCES = ("broad", "interest_biztools", "interest_niche")
OBJECTIVES = ("clicks", "pageviews", "leads", "sales")


def _schema(candidate_count: int) -> dict[str, Any]:
    scene = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "start_second": {"type": "number"},
            "end_second": {"type": "number"},
            "content": {"type": "string"},
        },
        "required": ["start_second", "end_second", "content"],
    }
    ad = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "headline": {"type": "string"},
            "primary_text": {"type": "string"},
            "call_to_action": {"type": "string"},
            "duration_seconds": {"type": "number"},
            "scenes": {"type": "array", "minItems": 1, "maxItems": 8, "items": scene},
            "portfolio_role": {"type": "string"},
        },
        "required": [
            "headline", "primary_text", "call_to_action", "duration_seconds", "scenes",
            "portfolio_role",
        ],
    }
    policy = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "strategy": {"type": "string"},
            "objective": {"type": "string", "enum": list(OBJECTIVES)},
            "audience": {"type": "string", "enum": list(AUDIENCES)},
            "ads": {"type": "array", "minItems": 3, "maxItems": 3, "items": ad},
            "ad_spend_shares": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {"type": "number"},
            },
        },
        "required": ["strategy", "objective", "audience", "ads", "ad_spend_shares"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "search_thesis": {"type": "string"},
            "policies": {
                "type": "array",
                "minItems": candidate_count,
                "maxItems": candidate_count,
                "items": policy,
            },
        },
        "required": ["search_thesis", "policies"],
    }


def _reward_feedback(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for result in sorted(results, key=lambda row: row["first_payment_roas"], reverse=True)[:2]:
        cohorts = result["hidden_audit"]["cohort_rows"]
        weakest = sorted(
            cohorts,
            key=lambda row: row["purchases"] / row["exposed"] if row["exposed"] else 0,
        )[:12]
        output.append({
            "policy": result["policy"],
            "receipt": public_receipt(result),
            "population_summaries": result["hidden_audit"]["population_summaries"],
            "weakest_cohort_ad_paths": [
                {
                    "parent": row["parent_id"],
                    "child": row["child_id"],
                    "ad": row["ad_id"],
                    "meaningful_views": row["meaningful_views"],
                    "purchases": row["purchases"],
                    "content_seen": row["content_seen"],
                    "decision": row["decision"],
                    "blocker": row["primary_blocker"],
                }
                for row in weakest
            ],
        })
    return output


def _prompt(
    *, world: Mapping[str, Any], platform: Mapping[str, Any], model: Mapping[str, Any],
    landing_page: str, generation: int, candidate_count: int, budget: float,
    periods: int, feedback: Sequence[Mapping[str, Any]],
    variation: Mapping[str, Any] | None,
) -> str:
    history = (
        "No prior policies exist. Explore substantively different strong solutions."
        if not feedback
        else f"""Repair the strongest policies using their exact hidden feedback. Candidate one
must be a disciplined refinement of the winner. Other candidates must test materially different
solutions, not paraphrases.

<reward_feedback_json>
{json.dumps(feedback, indent=2, ensure_ascii=False)}
</reward_feedback_json>"""
    )
    return f"""You are the omniscient optimizer for a simulated Meta advertising market.

Return exactly {candidate_count} COMPLETE policies. Each has three publication-ready timeline
ads and a real delivery allocation. The policy repeats for {periods} periods at ${budget:.2f}
per period. Maximize first-payment ROAS: settled first payments at ${float(world['price_usd']):.2f}
divided by spend. Apply the exact offer supplied below. You see the complete population hierarchy
and delivery world because this is an attainability oracle, not the hidden-information learner.

Optimize for people, not the evaluator. The first second must earn attention; a late argument
only affects viewers still watching. Every scene must be continuous from second 0 through the
declared duration. Show only landing-page-supported product behavior and make the click-through
coherent. Use no fabricated UI capability, adoption count, guarantee, discount, or testimonial.
Each ad needs a distinct portfolio role and 10%-70% of spend. ad_spend_shares are relative.
Choose the objective and audience using the revealed reach and delivery mixture, but do not
confuse a targeting cell with the policy.

<population_model_json>
{json.dumps(model, indent=2, ensure_ascii=False)}
</population_model_json>

<cross_cutting_human_variation_json>
{json.dumps(variation or {}, indent=2, ensure_ascii=False)}
</cross_cutting_human_variation_json>

<world_delivery_json>
{json.dumps(world, indent=2, ensure_ascii=False)}
</world_delivery_json>

<platform_json>
{json.dumps(platform, indent=2, ensure_ascii=False)}
</platform_json>

<landing_page>
{landing_page}
</landing_page>

{history}

This is search generation {generation}.
"""


def _normalize(
    raw: Any, *, generation: int, candidate_count: int, landing_page: str, budget: float,
) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("policies"), list):
        raise FullInfoPolicyError("designer returned an invalid batch")
    if len(raw["policies"]) != candidate_count:
        raise FullInfoPolicyError(f"designer must return {candidate_count} policies")
    policies = []
    for policy_index, value in enumerate(raw["policies"], start=1):
        if len(value["ads"]) != 3 or len(value["ad_spend_shares"]) != 3:
            raise FullInfoPolicyError("each policy requires three ads and shares")
        shares = [float(number) for number in value["ad_spend_shares"]]
        if any(number <= 0 for number in shares) or sum(shares) <= 0:
            raise FullInfoPolicyError("spend shares must be positive")
        shares = [number / sum(shares) for number in shares]
        if min(shares) < 0.099 or max(shares) > 0.701:
            raise FullInfoPolicyError(f"policy violates the 10%-70% spend constraint: {shares}")
        ads = []
        for ad_index, ad in enumerate(value["ads"], start=1):
            ads.append({
                "id": f"g{generation}p{policy_index}a{ad_index}",
                "headline": str(ad["headline"]).strip(),
                "primary_text": str(ad["primary_text"]).strip(),
                "call_to_action": str(ad["call_to_action"]).strip(),
                "duration_seconds": float(ad["duration_seconds"]),
                "scenes": [dict(scene) for scene in ad["scenes"]],
                "portfolio_role": str(ad["portfolio_role"]).strip(),
            })
        ad_ids = [ad["id"] for ad in ads]
        policies.append({
            "id": f"generation-{generation}-policy-{policy_index}",
            "strategy": str(value["strategy"]).strip(),
            "landing_page": landing_page,
            "ads": ads,
            "campaigns": [{
                "id": f"g{generation}p{policy_index}-campaign",
                "objective": str(value["objective"]),
                "mode": "fixed",
                "audience": str(value["audience"]),
                "budget": budget,
                "ad_ids": ad_ids,
                "ad_weights": dict(zip(ad_ids, shares)),
            }],
        })
    return str(raw.get("search_thesis") or "").strip(), policies


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def run_search(
    *, world_number: int, landing_page: str, budget: float, periods: int,
    generations: int, candidate_count: int, sample_replays: int, run_seed: int,
    designer: StructuredLLM, judge: StructuredLLM, judge_concurrency: int,
    output_dir: Path, model_path: Path = MODEL_PATH,
    variation_path: Path | None = None,
    economics_path: Path | None = None,
    choice_calibration_path: Path = CHOICE_CALIBRATION_PATH,
) -> dict[str, Any]:
    if budget <= 0 or periods < 1 or generations < 1 or not 2 <= candidate_count <= 5:
        raise FullInfoPolicyError("invalid search dimensions")
    world, platform = _load_world(world_number)
    model = load_population_model(model_path)
    variation = load_variation_model(variation_path)
    output_dir.mkdir(parents=True, exist_ok=False)
    all_results = []
    generation_summaries = []
    feedback: list[dict[str, Any]] = []
    for generation in range(1, generations + 1):
        raw = designer.complete(
            prompt=_prompt(
                world=world,
                platform=platform,
                model=model,
                landing_page=landing_page,
                generation=generation,
                candidate_count=candidate_count,
                budget=budget,
                periods=periods,
                feedback=feedback,
                variation=variation,
            ),
            schema=_schema(candidate_count),
            cache_namespace=f"full-info-policy-v2-designer-g{generation}",
        )
        thesis, policies = _normalize(
            raw,
            generation=generation,
            candidate_count=candidate_count,
            landing_page=landing_page,
            budget=budget,
        )
        results = evaluate_policies(
            world_number=world_number,
            raw_policies=policies,
            judge=judge,
            periods=periods,
            concurrency=judge_concurrency,
            expected_budget=budget,
            model_path=model_path,
            variation_path=variation_path,
            economics_path=economics_path,
            choice_calibration_path=choice_calibration_path,
        )
        all_results.extend(results)
        feedback = _reward_feedback(results)
        generation_summaries.append({
            "generation": generation,
            "search_thesis": thesis,
            "policies": policies,
            "receipts": [public_receipt(result) for result in results],
        })
        _write_json(output_dir / f"generation-{generation}-hidden.json", {
            "search_thesis": thesis,
            "policies": policies,
            "results": results,
        })
    winner = max(all_results, key=lambda result: result["first_payment_roas"])
    samples = [
        sampled_replay(winner, seed=world_number * 100_000 + run_seed * 1000 + index)
        for index in range(1, sample_replays + 1)
    ]
    sampled_roas = [sample["roas"] for sample in samples]
    summary = {
        "schema": "takyon.full-info-policy-search.v2",
        "world": world_number,
        "population_model": model.get("schema"),
        "human_variation_model": (variation or {}).get("schema"),
        "run_seed": run_seed,
        "budget_per_period": budget,
        "periods": periods,
        "generations": generations,
        "candidate_count_per_generation": candidate_count,
        "policies_evaluated": len(all_results),
        "population_evaluations": generations * 10,
        "winner_policy": winner["policy"],
        "expected_funnel": winner["funnel"],
        "expected_first_payment_revenue": winner["first_payment_revenue"],
        "expected_first_payment_roas": winner["first_payment_roas"],
        "subscription_roas_scenarios": winner["subscription_roas_scenarios"],
        "sample_replays": sample_replays,
        "sampled_roas_mean": sum(sampled_roas) / len(sampled_roas) if sampled_roas else None,
        "sampled_roas_min": min(sampled_roas) if sampled_roas else None,
        "sampled_roas_max": max(sampled_roas) if sampled_roas else None,
        "target_six_met": winner["first_payment_roas"] > 6,
        "designer": {"identity": designer.config.identity, "stats": designer.stats.record()},
        "judge": {"identity": judge.config.identity, "stats": judge.stats.record()},
        "output_dir": str(output_dir),
    }
    _write_json(output_dir / "search.json", generation_summaries)
    _write_json(output_dir / "winner-hidden-audit.json", winner)
    _write_json(output_dir / "sampled-replays.json", samples)
    _write_json(output_dir / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", type=int)
    parser.add_argument("--landing-page", type=Path, required=True)
    parser.add_argument("--budget", type=float, default=200.0)
    parser.add_argument("--periods", type=int, default=8)
    parser.add_argument("--generations", type=int, default=2)
    parser.add_argument("--candidate-count", type=int, default=4)
    parser.add_argument("--sample-replays", type=int, default=100)
    parser.add_argument("--run-seed", type=int, default=1)
    parser.add_argument("--designer-provider", choices=("codex", "openai"), default="codex")
    parser.add_argument("--designer-model", required=True)
    parser.add_argument("--designer-base-url", default="")
    parser.add_argument("--designer-api-key-env", default="TIER_B_AGENT_API_KEY")
    parser.add_argument("--judge-provider", choices=("codex", "openai"), default="codex")
    parser.add_argument("--judge-model", required=True)
    parser.add_argument("--judge-base-url", default="")
    parser.add_argument("--judge-api-key-env", default="TIER_B_JUDGE_API_KEY")
    parser.add_argument("--judge-concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--cache-dir", type=Path, default=SIM_ROOT / "cache" / "population-v3-choice")
    parser.add_argument("--output-root", type=Path, default=SIM_ROOT / "runs" / "population-v3-choice")
    parser.add_argument("--codex-bin", default="")
    parser.add_argument(
        "--population-model",
        type=Path,
        required=True,
        help=(
            "Frozen population-model.json to run against. Required so a run can "
            "never silently fall back to the hand-written default; pass "
            f"{MODEL_PATH} explicitly to use the static v3 model."
        ),
    )
    parser.add_argument("--human-variation-model", type=Path)
    parser.add_argument("--subscription-economics", type=Path)
    parser.add_argument("--choice-calibration", type=Path, default=CHOICE_CALIBRATION_PATH)
    args = parser.parse_args(argv)
    designer = StructuredLLM(LLMConfig(
        provider=args.designer_provider,
        model=args.designer_model,
        base_url=args.designer_base_url,
        api_key_env=args.designer_api_key_env,
        timeout_seconds=args.timeout,
        max_output_tokens=24000,
        codex_bin=args.codex_bin,
    ), response_cache_dir=args.cache_dir / "llm")
    judge = StructuredLLM(LLMConfig(
        provider=args.judge_provider,
        model=args.judge_model,
        base_url=args.judge_base_url,
        api_key_env=args.judge_api_key_env,
        timeout_seconds=args.timeout,
        max_output_tokens=20000,
        codex_bin=args.codex_bin,
    ), response_cache_dir=args.cache_dir / "llm")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_root / f"world-{args.world}-seed-{args.run_seed}-{stamp}"
    summary = run_search(
        world_number=args.world,
        landing_page=args.landing_page.read_text(encoding="utf-8"),
        budget=args.budget,
        periods=args.periods,
        generations=args.generations,
        candidate_count=args.candidate_count,
        sample_replays=args.sample_replays,
        run_seed=args.run_seed,
        designer=designer,
        judge=judge,
        judge_concurrency=args.judge_concurrency,
        output_dir=output_dir,
        model_path=args.population_model,
        variation_path=args.human_variation_model,
        economics_path=args.subscription_economics,
        choice_calibration_path=args.choice_calibration,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
