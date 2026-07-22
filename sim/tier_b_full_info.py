"""Construct and run a fixed Tier-B action with the hidden world fully revealed.

This is an attainability control, not a learning experiment: an agent sees the
hidden personas, delivery mechanics, landing page, price, and offer; proposes a
candidate creative set once; and the harness selects the best expected
creative/objective/audience cells before any sampled batch is drawn.  No
semantic gradient or receipt feedback is used.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Mapping

try:
    from .llm_client import LLMConfig, StructuredLLM
    from .tier_b_market import SIM_ROOT, _load_world, judge_ads, simulate, validate_spec
except ImportError:  # pragma: no cover - direct-script path
    from llm_client import LLMConfig, StructuredLLM
    from tier_b_market import SIM_ROOT, _load_world, judge_ads, simulate, validate_spec


class FullInfoError(RuntimeError):
    pass


def _candidate_schema(count: int) -> dict[str, Any]:
    ad = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string"},
            "headline": {"type": "string"},
            "message": {"type": "string"},
            "visual": {"type": "string"},
            "call_to_action": {"type": "string"},
            "proof_tag": {
                "type": "string",
                "enum": ["benefit", "outcome", "count", "story"],
            },
            "named_story": {"type": "boolean"},
            "demo": {"type": "boolean"},
            "full_info_rationale": {"type": "string"},
        },
        "required": [
            "id",
            "headline",
            "message",
            "visual",
            "call_to_action",
            "proof_tag",
            "named_story",
            "demo",
            "full_info_rationale",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "strategy": {"type": "string"},
            "ads": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": ad,
            },
        },
        "required": ["strategy", "ads"],
    }


def _candidate_prompt(
    *, world: Mapping[str, Any], platform: Mapping[str, Any], landing_page: str,
    candidate_count: int, generation: int,
    elite_feedback: list[dict[str, Any]],
) -> str:
    feedback = (
        "No earlier candidates exist. Construct the strongest initial population."
        if not elite_feedback
        else f"""Here are the strongest earlier candidates with exact judge rates and reasons.
Candidate 1 must reproduce the strongest candidate's headline, message, visual, and CTA
verbatim as the incumbent control. Use the other candidates to repair its conversion losses.
Converge aggressively: focused local variants and single-change rewrites are encouraged.
This is offline reward-model optimization, so do not preserve creative diversity for its
own sake.

<elite_reward_feedback_json>
{json.dumps(elite_feedback, indent=2, ensure_ascii=False)}
</elite_reward_feedback_json>"""
    )
    return f"""You are constructing an omniscient upper-bound action for an advertising simulator.

This is deliberately not a hidden-information learning test. You have the complete hidden
market: persona weights, subpopulation reach, delivery affinities, response affinities,
price, offer friction, landing page, and platform mechanics. Use every field aggressively
to maximize expected settled-purchase ROAS. There will be no later revision and no semantic
gradient.

This is search generation {generation}. Return exactly {candidate_count} genuinely different,
publication-ready candidate ads.
They are an offline candidate set: a deterministic evaluator will score every candidate
against every objective and audience and construct the final fixed action before sampled
traffic begins. Optimize the actual words and visual description, not metadata. Make claims
consistent with the landing page. A visible product demonstration may be used whenever the
hidden personas favor it. Use a named story only if it remains credible from supplied facts.

<fully_revealed_hidden_market_json>
{json.dumps(world, indent=2, ensure_ascii=False)}
</fully_revealed_hidden_market_json>

<fully_revealed_platform_json>
{json.dumps(platform, indent=2, ensure_ascii=False)}
</fully_revealed_platform_json>

<landing_page>
{landing_page}
</landing_page>

{feedback}
"""


def _normalize_candidates(
    payload: Any, count: int, generation: int,
) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("ads"), list):
        raise FullInfoError("candidate agent returned an invalid object")
    if len(payload["ads"]) != count:
        raise FullInfoError(f"candidate agent must return exactly {count} ads")
    ads = []
    for index, raw in enumerate(payload["ads"], start=1):
        if not isinstance(raw, Mapping):
            raise FullInfoError("candidate agent returned a non-object ad")
        ad = {
            "id": f"g{generation}-{str(raw.get('id') or f'candidate-{index}').strip()}",
            "headline": str(raw.get("headline") or "").strip(),
            "message": str(raw.get("message") or "").strip(),
            "visual": str(raw.get("visual") or "").strip(),
            "call_to_action": str(raw.get("call_to_action") or "LEARN_MORE").strip(),
            "proof": str(raw.get("proof_tag") or "benefit").strip(),
            "named_story": bool(raw.get("named_story")),
            "demo": bool(raw.get("demo")),
            "full_info_rationale": str(raw.get("full_info_rationale") or "").strip(),
            "search_generation": generation,
        }
        if not ad["headline"] or len(ad["message"]) < 20 or len(ad["visual"]) < 20:
            raise FullInfoError(f"candidate {index} is not publication-ready")
        ads.append(ad)
    if len({ad["id"] for ad in ads}) != count:
        raise FullInfoError("candidate ids must be unique")
    return str(payload.get("strategy") or "").strip(), ads


def _single_cell_spec(
    *, ad: Mapping[str, Any], objective: str, audience: str, budget: float,
    landing_page: str,
) -> dict[str, Any]:
    return {
        "iteration": 0,
        "policy": "full-info-candidate-evaluation",
        "landing_page": landing_page,
        "ads": [dict(ad)],
        "campaigns": [{
            "id": f"{objective}-{audience}",
            "objective": objective,
            "audience": audience,
            "mode": "fixed",
            "budget": budget,
            "ad_ids": [ad["id"]],
        }],
    }


def _build_action(
    *, frontier: list[dict[str, Any]], ads_by_id: Mapping[str, Mapping[str, Any]],
    landing_page: str, budget: float,
) -> dict[str, Any]:
    selected = []
    seen_ads: set[str] = set()
    for row in frontier:
        if row["ad_id"] not in seen_ads:
            selected.append(row)
            seen_ads.add(row["ad_id"])
        if len(selected) == 3:
            break
    if len(selected) != 3:
        raise FullInfoError("at least three distinct candidates are required")
    reserve = 0.01
    allocations = [round(budget - 2 * reserve, 2), reserve, reserve]
    return {
        "iteration": 1,
        "policy": "full-info-fixed-action",
        "landing_page": landing_page,
        "ads": [dict(ads_by_id[row["ad_id"]]) for row in selected],
        "campaigns": [
            {
                "id": f"oracle-{index}-{row['objective']}-{row['audience']}",
                "objective": row["objective"],
                "audience": row["audience"],
                "mode": "fixed",
                "budget": allocation,
                "ad_ids": [row["ad_id"]],
            }
            for index, (row, allocation) in enumerate(zip(selected, allocations), start=1)
        ],
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def run_full_info(
    *, world_number: int, landing_page: str, budget: float, batches: int,
    run_seed: int, candidate_count: int, generations: int, target_roas: float,
    agent: StructuredLLM, judge: StructuredLLM, cache_dir: Path, output_dir: Path,
    judge_batch_pairs: int, judge_concurrency: int,
    initial_elite_feedback: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if budget <= 0.02 or batches < 1 or not 3 <= candidate_count <= 6 or generations < 1:
        raise FullInfoError(
            "budget must exceed $0.02; batches and generations must be positive; "
            "candidate_count must be between 3 and 6"
        )
    world, platform = _load_world(world_number)
    output_dir.mkdir(parents=True, exist_ok=False)
    strategies = []
    ads = []
    ads_by_id: dict[str, dict[str, Any]] = {}
    judgments_by_ad: dict[str, list[dict[str, Any]]] = {}
    frontier = []
    elite_feedback: list[dict[str, Any]] = list(initial_elite_feedback or [])
    for generation in range(1, generations + 1):
        raw = agent.complete(
            prompt=_candidate_prompt(
                world=world,
                platform=platform,
                landing_page=landing_page,
                candidate_count=candidate_count,
                generation=generation,
                elite_feedback=elite_feedback,
            ),
            schema=_candidate_schema(candidate_count),
            cache_namespace=f"tier-b-full-info-candidates-g{generation}",
        )
        strategy, generation_ads = _normalize_candidates(
            raw, candidate_count, generation
        )
        strategies.append({"generation": generation, "strategy": strategy})
        ads.extend(generation_ads)
        ads_by_id.update({ad["id"]: ad for ad in generation_ads})

        # Judge the whole generation concurrently before sweeping delivery cells.
        population_spec = validate_spec({
            "iteration": 0,
            "policy": f"full-info-search-g{generation}",
            "landing_page": landing_page,
            "ads": generation_ads,
            "campaigns": [{
                "id": f"prejudge-g{generation}",
                "objective": "sales",
                "audience": "broad",
                "mode": "fixed",
                "budget": budget,
                "ad_ids": [ad["id"] for ad in generation_ads],
            }],
        }, platform)
        judged = judge_ads(
            world_number=world_number,
            world=world,
            spec=population_spec,
            judge=judge,
            cache_dir=cache_dir / "tier-b",
            batch_pairs=judge_batch_pairs,
            concurrency=judge_concurrency,
        )
        for ad in generation_ads:
            judgments_by_ad[ad["id"]] = [
                judgment for (persona_id, ad_id), judgment in sorted(judged.items())
                if ad_id == ad["id"]
            ]
            for objective in platform["objectives"]:
                for audience in platform["audiences"]:
                    spec = validate_spec(
                        _single_cell_spec(
                            ad=ad,
                            objective=objective,
                            audience=audience,
                            budget=budget,
                            landing_page=landing_page,
                        ),
                        platform,
                    )
                    result = simulate(
                        world_number=world_number,
                        seed=world_number * 100_000 + run_seed * 100 + 90,
                        raw_spec=spec,
                        judge=judge,
                        cache_dir=cache_dir / "tier-b",
                        batch_pairs=judge_batch_pairs,
                        concurrency=judge_concurrency,
                        expected=True,
                    )
                    frontier.append({
                        "generation": generation,
                        "ad_id": ad["id"],
                        "objective": objective,
                        "audience": audience,
                        "expected_roas": result["roas"],
                        "expected_revenue": result["revenue"],
                    })
        best_by_ad: dict[str, dict[str, Any]] = {}
        for row in sorted(frontier, key=lambda value: value["expected_roas"], reverse=True):
            best_by_ad.setdefault(row["ad_id"], row)
        elite_feedback = [
            {
                "ad": ads_by_id[row["ad_id"]],
                "best_cell": row,
                "persona_judgments": judgments_by_ad[row["ad_id"]],
            }
            for row in list(best_by_ad.values())[:3]
        ]
    frontier.sort(key=lambda row: row["expected_roas"], reverse=True)
    action = validate_spec(
        _build_action(
            frontier=frontier,
            ads_by_id=ads_by_id,
            landing_page=landing_page,
            budget=budget,
        ),
        platform,
    )
    expected = simulate(
        world_number=world_number,
        seed=world_number * 100_000 + run_seed * 100 + 90,
        raw_spec=action,
        judge=judge,
        cache_dir=cache_dir / "tier-b",
        batch_pairs=judge_batch_pairs,
        concurrency=judge_concurrency,
        expected=True,
    )
    sampled = []
    for batch in range(1, batches + 1):
        batch_spec = {**action, "iteration": batch}
        sampled.append(simulate(
            world_number=world_number,
            seed=world_number * 100_000 + run_seed * 100 + batch,
            raw_spec=batch_spec,
            judge=judge,
            cache_dir=cache_dir / "tier-b",
            batch_pairs=judge_batch_pairs,
            concurrency=judge_concurrency,
        ))
    sampled_spend = sum(result["spend"] for result in sampled)
    sampled_revenue = sum(result["revenue"] for result in sampled)
    sampled_roas = sampled_revenue / sampled_spend if sampled_spend else 0.0
    summary = {
        "schema": "takyon.tier-b-full-info.v1",
        "world": world_number,
        "run_seed": run_seed,
        "strategies": strategies,
        "candidate_count_per_generation": candidate_count,
        "generations": generations,
        "total_candidates": len(ads),
        "batches": batches,
        "budget_per_batch": budget,
        "target_roas": target_roas,
        "expected_roas": expected["roas"],
        "expected_revenue_per_batch": expected["revenue"],
        "sampled_roas": sampled_roas,
        "sampled_revenue": sampled_revenue,
        "sampled_spend": sampled_spend,
        "target_met_expected": expected["roas"] >= target_roas,
        "target_met_sampled": sampled_roas >= target_roas,
        "winning_cell": frontier[0],
        "agent": {"identity": agent.config.identity, "stats": agent.stats.record()},
        "judge": {"identity": judge.config.identity, "stats": judge.stats.record()},
        "output_dir": str(output_dir),
    }
    _write_json(output_dir / "candidates.json", {"strategies": strategies, "ads": ads})
    _write_json(output_dir / "judgments.json", judgments_by_ad)
    _write_json(output_dir / "frontier.json", frontier)
    _write_json(output_dir / "action.json", action)
    _write_json(output_dir / "expected.json", expected)
    _write_json(output_dir / "sampled.json", sampled)
    _write_json(output_dir / "summary.json", summary)
    return summary


def _load_elite_feedback(run_dir: Path) -> list[dict[str, Any]]:
    candidates = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))
    frontier = json.loads((run_dir / "frontier.json").read_text(encoding="utf-8"))
    judgments = json.loads((run_dir / "judgments.json").read_text(encoding="utf-8"))
    ads = {ad["id"]: ad for ad in candidates["ads"]}
    best_by_ad: dict[str, dict[str, Any]] = {}
    for row in frontier:
        best_by_ad.setdefault(row["ad_id"], row)
    return [
        {
            "ad": ads[row["ad_id"]],
            "best_cell": row,
            "persona_judgments": judgments[row["ad_id"]],
        }
        for row in list(best_by_ad.values())[:3]
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", type=int)
    parser.add_argument("--landing-page", type=Path, required=True)
    parser.add_argument("--budget", type=float, default=200.0)
    parser.add_argument("--batches", type=int, default=8)
    parser.add_argument("--run-seed", type=int, default=1)
    parser.add_argument("--candidate-count", type=int, default=6)
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument("--target-roas", type=float, default=1.0)
    parser.add_argument("--agent-provider", choices=("codex", "openai"), default="codex")
    parser.add_argument("--agent-model", required=True)
    parser.add_argument("--agent-base-url", default="")
    parser.add_argument("--agent-api-key-env", default="TIER_B_AGENT_API_KEY")
    parser.add_argument("--judge-provider", choices=("codex", "openai"), default="codex")
    parser.add_argument("--judge-model", required=True)
    parser.add_argument("--judge-base-url", default="")
    parser.add_argument("--judge-api-key-env", default="TIER_B_JUDGE_API_KEY")
    parser.add_argument("--judge-batch-pairs", type=int, default=10)
    parser.add_argument("--judge-concurrency", type=int, default=3)
    parser.add_argument("--cache-dir", type=Path, default=SIM_ROOT / "cache")
    parser.add_argument("--output-root", type=Path, default=SIM_ROOT / "runs" / "full-info")
    parser.add_argument("--seed-run", type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--codex-bin", default="")
    args = parser.parse_args(argv)

    landing_page = args.landing_page.read_text(encoding="utf-8")
    agent = StructuredLLM(LLMConfig(
        provider=args.agent_provider,
        model=args.agent_model,
        base_url=args.agent_base_url,
        api_key_env=args.agent_api_key_env,
        timeout_seconds=args.timeout,
        max_output_tokens=16000,
        codex_bin=args.codex_bin,
    ), response_cache_dir=args.cache_dir / "llm")
    judge = StructuredLLM(LLMConfig(
        provider=args.judge_provider,
        model=args.judge_model,
        base_url=args.judge_base_url,
        api_key_env=args.judge_api_key_env,
        timeout_seconds=args.timeout,
        max_output_tokens=12000,
        codex_bin=args.codex_bin,
    ), response_cache_dir=args.cache_dir / "llm")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_root / f"world-{args.world}-seed-{args.run_seed}-{stamp}"
    initial_elite_feedback = (
        _load_elite_feedback(args.seed_run) if args.seed_run else None
    )
    summary = run_full_info(
        world_number=args.world,
        landing_page=landing_page,
        budget=args.budget,
        batches=args.batches,
        run_seed=args.run_seed,
        candidate_count=args.candidate_count,
        generations=args.generations,
        target_roas=args.target_roas,
        agent=agent,
        judge=judge,
        cache_dir=args.cache_dir,
        output_dir=output_dir,
        judge_batch_pairs=args.judge_batch_pairs,
        judge_concurrency=args.judge_concurrency,
        initial_elite_feedback=initial_elite_feedback,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
