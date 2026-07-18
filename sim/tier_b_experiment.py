"""Run a complete hidden-persona Tier-B semantic-policy experiment.

Each iteration is fully executable:

1. An isolated policy agent designs actual ads and a budgeted campaign portfolio.
2. A separate hidden-persona LLM judge produces cached response distributions.
3. The market exposes personas through audience/objective mechanics and samples receipts.
4. The policy agent applies the canonical semantic-gradient method and emits six policies.
5. A seeded noise schedule selects keep or one policy dose before adoption.

The policy agent never receives hidden personas, judge outputs, or hidden world files.
There is no heuristic LLM fallback and no fabricated receipt path.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

try:  # package import in tests; direct import when run as `python sim/...py`
    from .llm_client import LLMConfig, LLMError, StructuredLLM
    from .noise_schedule import PRESETS, NoiseSchedule
    from .tier_b_market import SIM_ROOT, TierBError, _load_world, simulate, validate_spec
except ImportError:  # pragma: no cover - direct-script path
    from llm_client import LLMConfig, LLMError, StructuredLLM
    from noise_schedule import PRESETS, NoiseSchedule
    from tier_b_market import SIM_ROOT, TierBError, _load_world, simulate, validate_spec


REPO_ROOT = SIM_ROOT.parent
SEMANTIC_GRADIENT_PATH = REPO_ROOT / "ad-creative-stack" / "semantic-gradient.md"


class ExperimentError(RuntimeError):
    pass


def _design_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "design_thesis": {"type": "string"},
            "ads": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {
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
                    ],
                },
            },
            "campaigns": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "objective": {
                            "type": "string",
                            "enum": ["clicks", "pageviews", "leads", "sales"],
                        },
                        "mode": {"type": "string", "enum": ["fixed", "auto"]},
                        "audience": {"type": "string"},
                        "audiences": {"type": "array", "items": {"type": "string"}},
                        "ad_ids": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 3,
                            "items": {"type": "string"},
                        },
                        "budget": {"type": "number"},
                    },
                    "required": [
                        "id",
                        "objective",
                        "mode",
                        "audience",
                        "audiences",
                        "ad_ids",
                        "budget",
                    ],
                },
            },
        },
        "required": ["design_thesis", "ads", "campaigns"],
    }


def _gradient_schema(rungs: int = 6) -> dict[str, Any]:
    change_map = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "creative": {"type": "string"},
            "campaigns": {"type": "string"},
            "judgment": {"type": "string"},
            "experimentation": {"type": "string"},
        },
        "required": ["creative", "campaigns", "judgment", "experimentation"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "thesis": {"type": "string"},
            "mechanism": {"type": "string"},
            "evidence_basis": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "matched_pairs": {"type": "array", "items": {"type": "string"}},
                    "replicated_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "design_evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["matched_pairs", "replicated_patterns", "design_evidence"],
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "breadth": {"type": "number", "minimum": 0, "maximum": 1},
            "falsifier": {"type": "string"},
            "rungs": {
                "type": "array",
                "minItems": rungs,
                "maxItems": rungs,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "dose": {"type": "integer"},
                        "change_summary": {"type": "string"},
                        "change_map": change_map,
                        "policy": {"type": "string"},
                    },
                    "required": ["dose", "change_summary", "change_map", "policy"],
                },
            },
        },
        "required": [
            "thesis",
            "mechanism",
            "evidence_basis",
            "confidence",
            "breadth",
            "falsifier",
            "rungs",
        ],
    }


def _evidence_for_agent(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "iteration": record["iteration"],
            "policy_version": record["policy_version"],
            "policy": record["policy"],
            "design_thesis": record["design_thesis"],
            "ads": record["spec"]["ads"],
            "campaigns": record["spec"]["campaigns"],
            "receipt": {
                "rows": record["result"]["rows"],
                "funnel": record["result"]["funnel"],
                "spend": record["result"]["spend"],
                "revenue": record["result"]["revenue"],
                "roas": record["result"]["roas"],
            },
        }
        for record in history
    ]


def _design_prompt(
    *, iteration: int, policy_version: int, policy: str, goal: str,
    landing_page: str, platform: Mapping[str, Any], budget: float,
    history: list[dict[str, Any]],
) -> str:
    return f"""You are the advertising experiment designer inside a hidden-market adaptation test.

You do not know the hidden personas. Infer them only from prior aggregate receipts. Design the next batch under the current policy. Return three genuinely different, publication-ready ads with exact headline, complete primary message, CTA, and a concrete visual/video description. The hidden judge evaluates the actual words and visual description; metadata labels have no effect.

Use only information supplied inside this prompt. Do not use tools, inspect local files, or seek hidden world, judge, cache, generator, seed, or oracle data.

Use the current policy faithfully. Preserve exploration coverage when evidence is thin. Do not optimize signup proxies over settled purchases. Compare prior results only within matched objective/audience cells. Campaign budgets must sum to exactly {budget:.2f}. Every campaign must list the IDs of its eligible ads under ad_ids, and every returned ad must be eligible in at least one campaign. Fixed campaigns use one audience and an empty audiences array. Auto campaigns use an empty audience string and at least two audiences. Available audiences and CPMs are in the platform JSON.

GOAL: {goal}
ITERATION: {iteration}
POLICY VERSION: v{policy_version}

<current_policy>
{policy}
</current_policy>

<landing_page>
{landing_page}
</landing_page>

<platform_json>
{json.dumps(platform, indent=2, ensure_ascii=False)}
</platform_json>

<full_prior_evidence_json>
{json.dumps(_evidence_for_agent(history), indent=2, ensure_ascii=False)}
</full_prior_evidence_json>
"""


def _normalize_design(
    payload: Any, *, iteration: int, policy_version: int, landing_page: str,
    platform: Mapping[str, Any], budget: float,
) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ExperimentError("design agent returned a non-object")
    ads = payload.get("ads")
    campaigns = payload.get("campaigns")
    if not isinstance(ads, list) or len(ads) != 3:
        raise ExperimentError("design agent must return exactly three ads")
    if not isinstance(campaigns, list) or not campaigns:
        raise ExperimentError("design agent returned no campaigns")
    normalized_ads = []
    for index, ad in enumerate(ads):
        if not isinstance(ad, Mapping):
            raise ExperimentError("design agent returned an invalid ad")
        normalized_ads.append(
            {
                "id": str(ad.get("id") or f"it{iteration}-ad{index + 1}"),
                "headline": str(ad.get("headline") or "").strip(),
                "message": str(ad.get("message") or "").strip(),
                "visual": str(ad.get("visual") or "").strip(),
                "call_to_action": str(ad.get("call_to_action") or "LEARN_MORE").strip(),
                # Tags are recorded for coverage analysis but hidden from the Tier-B judge.
                "proof": str(ad.get("proof_tag") or "benefit"),
                "named_story": bool(ad.get("named_story")),
                "demo": bool(ad.get("demo")),
            }
        )
    ad_ids = [ad["id"] for ad in normalized_ads]
    if len(set(ad_ids)) != len(ad_ids):
        raise ExperimentError("design agent returned duplicate ad ids")
    normalized_campaigns = []
    assigned_ad_ids: set[str] = set()
    raw_total = 0.0
    for campaign in campaigns:
        if not isinstance(campaign, Mapping):
            raise ExperimentError("design agent returned an invalid campaign")
        try:
            amount = float(campaign["budget"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExperimentError("design agent returned an invalid campaign budget") from exc
        if amount <= 0:
            raise ExperimentError("design agent returned a non-positive campaign budget")
        raw_total += amount
    if raw_total <= 0:
        raise ExperimentError("design campaign budget total is zero")
    remaining = round(budget, 2)
    for index, campaign in enumerate(campaigns):
        amount = (
            remaining
            if index == len(campaigns) - 1
            else round(float(campaign["budget"]) / raw_total * budget, 2)
        )
        remaining = round(remaining - amount, 2)
        mode = str(campaign.get("mode") or "fixed")
        normalized = {
            "id": str(campaign.get("id") or f"campaign-{index + 1}"),
            "objective": str(campaign.get("objective") or ""),
            "budget": amount,
            "mode": mode,
        }
        campaign_ad_ids = [str(value).strip() for value in campaign.get("ad_ids") or []]
        if (
            not campaign_ad_ids
            or len(set(campaign_ad_ids)) != len(campaign_ad_ids)
            or any(value not in ad_ids for value in campaign_ad_ids)
        ):
            raise ExperimentError("every campaign requires unique known ad_ids")
        normalized["ad_ids"] = campaign_ad_ids
        assigned_ad_ids.update(campaign_ad_ids)
        if mode == "auto":
            normalized["audiences"] = list(campaign.get("audiences") or [])
        else:
            normalized["audience"] = str(campaign.get("audience") or "")
        normalized_campaigns.append(normalized)
    if assigned_ad_ids != set(ad_ids):
        raise ExperimentError("every returned ad must be assigned to at least one campaign")
    spec = {
        "iteration": iteration,
        "policy": f"v{policy_version}",
        "landing_page": landing_page,
        "ads": normalized_ads,
        "campaigns": normalized_campaigns,
    }
    return str(payload.get("design_thesis") or "").strip(), validate_spec(spec, platform)


def _gradient_prompt(
    *, operator: str, goal: str, current_policy: str, current_version: int,
    history: list[dict[str, Any]],
) -> str:
    return f"""Execute the Semantic Gradient v2 method below exactly. The JSON transport replaces its markdown output format, but all diagnosis, evidence, coverage, composition, evidence-basis, confidence, breadth, change-map, and independent-dose rules remain binding.

Produce one falsifiable organizing thesis and exactly six complete standalone policies, ordered from the smallest implication to a clean whole-policy rewrite at rung 6. The thesis may combine matched-pair, replicated-pattern, and design evidence when all support one mechanism. Evidence types are not mutually exclusive classes and do not cap any rung. One thesis may have coordinated consequences across the policy; no rung may introduce a second explanation.

Every policy must be executable by this fixed action interface: exactly three complete ads; one to eight campaigns; objectives clicks, pageviews, leads, or sales; fixed campaigns with one audience; auto campaigns with at least two audiences; and explicit ad_ids controlling which of the three ads may run in each campaign. The available audiences are broad, interest_biztools, and interest_niche. Do not prescribe more ads, placements, schedules, delivery checks, mutable launch machinery, or controls outside this interface.

Use only information supplied inside this prompt. Do not use tools, inspect local files, or seek hidden world, judge, cache, generator, seed, or oracle data. The policy learner sees no hidden personas.

<semantic_gradient_operator>
{operator}
</semantic_gradient_operator>

GOAL: {goal}
CURRENT VERSION: v{current_version}

<current_policy>
{current_policy}
</current_policy>

<full_fidelity_evidence_json>
{json.dumps(_evidence_for_agent(history), indent=2, ensure_ascii=False)}
</full_fidelity_evidence_json>
"""


def _validate_gradient(payload: Any, *, current_policy: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ExperimentError("gradient agent returned a non-object")
    text_fields = ("thesis", "mechanism", "falsifier")
    if any(not str(payload.get(field) or "").strip() for field in text_fields):
        raise ExperimentError("gradient omitted its thesis, mechanism, or falsifier")
    evidence_basis = payload.get("evidence_basis")
    evidence_fields = ("matched_pairs", "replicated_patterns", "design_evidence")
    if not isinstance(evidence_basis, Mapping) or any(
        not isinstance(evidence_basis.get(field), list) for field in evidence_fields
    ):
        raise ExperimentError("gradient returned an invalid evidence basis")
    try:
        confidence = float(payload["confidence"])
        breadth = float(payload["breadth"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExperimentError("gradient returned invalid confidence or breadth") from exc
    if not 0 <= confidence <= 1 or not 0 <= breadth <= 1:
        raise ExperimentError("gradient confidence and breadth must be in [0, 1]")
    rungs = payload.get("rungs")
    if not isinstance(rungs, list) or len(rungs) != 6:
        raise ExperimentError("gradient must return exactly six policy rungs")
    normalized = []
    for index, rung in enumerate(rungs, start=1):
        if not isinstance(rung, Mapping) or int(rung.get("dose") or 0) != index:
            raise ExperimentError("gradient doses must be numbered 1 through 6")
        policy = str(rung.get("policy") or "").strip()
        if len(policy) < 160:
            raise ExperimentError(f"gradient dose {index} is not a complete policy")
        change_map = rung.get("change_map")
        change_surfaces = ("creative", "campaigns", "judgment", "experimentation")
        if not isinstance(change_map, Mapping) or any(
            not str(change_map.get(surface) or "").strip() for surface in change_surfaces
        ):
            raise ExperimentError(f"gradient dose {index} has an incomplete change map")
        normalized.append(
            {
                "dose": index,
                "change_summary": str(rung.get("change_summary") or "").strip(),
                "change_map": {
                    surface: str(change_map[surface]).strip()
                    for surface in change_surfaces
                },
                "policy": policy,
            }
        )
    if all(rung["policy"] == current_policy for rung in normalized):
        raise ExperimentError("gradient returned six unchanged policies")
    return {
        "thesis": str(payload["thesis"]).strip(),
        "mechanism": str(payload["mechanism"]).strip(),
        "evidence_basis": {
            field: [str(item).strip() for item in evidence_basis[field]]
            for field in evidence_fields
        },
        "confidence": confidence,
        "breadth": breadth,
        "falsifier": str(payload["falsifier"]).strip(),
        "rungs": normalized,
    }


def _safe_run_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "run"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def run_experiment(
    *, world_number: int, run_seed: int, iterations: int, budget: float,
    goal: str, landing_page: str, seed_policy: str, schedule: NoiseSchedule,
    schedule_name: str, judge: StructuredLLM, agent: StructuredLLM,
    cache_dir: Path, output_dir: Path, judge_batch_pairs: int,
    judge_concurrency: int,
) -> dict[str, Any]:
    if iterations < 1 or budget <= 0:
        raise ExperimentError("iterations and budget must be positive")
    _, platform = _load_world(world_number)
    operator = SEMANTIC_GRADIENT_PATH.read_text(encoding="utf-8")
    history: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    current_policy = seed_policy.strip()
    policy_version = 0
    first_spec: dict[str, Any] | None = None
    output_dir.mkdir(parents=True, exist_ok=False)

    for iteration in range(1, iterations + 1):
        design_payload = agent.complete(
            prompt=_design_prompt(
                iteration=iteration,
                policy_version=policy_version,
                policy=current_policy,
                goal=goal,
                landing_page=landing_page,
                platform=platform,
                budget=budget,
                history=history,
            ),
            schema=_design_schema(),
            cache_namespace="tier-b-design",
        )
        design_thesis, spec = _normalize_design(
            design_payload,
            iteration=iteration,
            policy_version=policy_version,
            landing_page=landing_page,
            platform=platform,
            budget=budget,
        )
        if first_spec is None:
            first_spec = spec
        result = simulate(
            world_number=world_number,
            seed=world_number * 100_000 + run_seed * 100 + iteration,
            raw_spec=spec,
            judge=judge,
            cache_dir=cache_dir / "tier-b",
            batch_pairs=judge_batch_pairs,
            concurrency=judge_concurrency,
        )
        record = {
            "iteration": iteration,
            "policy_version": policy_version,
            "policy": current_policy,
            "design_thesis": design_thesis,
            "spec": spec,
            "result": result,
        }
        history.append(record)
        gradient_payload = agent.complete(
            prompt=_gradient_prompt(
                operator=operator,
                goal=goal,
                current_policy=current_policy,
                current_version=policy_version,
                history=history,
            ),
            schema=_gradient_schema(),
            cache_namespace="tier-b-gradient",
        )
        gradient = _validate_gradient(gradient_payload, current_policy=current_policy)
        draw_seed = world_number * 1_000_000 + run_seed * 100 + iteration
        selected_rung = schedule.draw(iteration, draw_seed)
        prior_version = policy_version
        if selected_rung:
            current_policy = gradient["rungs"][selected_rung - 1]["policy"]
            policy_version += 1
        lineage.append(
            {
                "iteration": iteration,
                "parent_version": prior_version,
                "thesis": gradient["thesis"],
                "mechanism": gradient["mechanism"],
                "evidence_basis": gradient["evidence_basis"],
                "confidence": gradient["confidence"],
                "breadth": gradient["breadth"],
                "falsifier": gradient["falsifier"],
                "rungs": gradient["rungs"],
                "schedule_probabilities": schedule.probabilities(iteration),
                "draw_seed": draw_seed,
                "selected_rung": selected_rung,
                "adopted_version": policy_version,
            }
        )
        _write_json(output_dir / "evidence.json", _evidence_for_agent(history))
        _write_json(output_dir / "lineage.json", lineage)
        _write_json(output_dir / "specs" / f"iteration-{iteration}.json", spec)
        (output_dir / "current-policy.md").write_text(current_policy, encoding="utf-8")

    assert first_spec is not None
    final_design_payload = agent.complete(
        prompt=_design_prompt(
            iteration=iterations + 1,
            policy_version=policy_version,
            policy=current_policy,
            goal=goal,
            landing_page=landing_page,
            platform=platform,
            budget=budget,
            history=history,
        )
        + "\nThis is a held-out final evaluation design. Do not revise the policy.",
        schema=_design_schema(),
        cache_namespace="tier-b-final-design",
    )
    _, final_spec = _normalize_design(
        final_design_payload,
        iteration=iterations + 1,
        policy_version=policy_version,
        landing_page=landing_page,
        platform=platform,
        budget=budget,
    )
    initial_expected = simulate(
        world_number=world_number,
        seed=world_number * 100_000 + run_seed * 100 + 90,
        raw_spec=first_spec,
        judge=judge,
        cache_dir=cache_dir / "tier-b",
        batch_pairs=judge_batch_pairs,
        concurrency=judge_concurrency,
        expected=True,
    )
    final_expected = simulate(
        world_number=world_number,
        seed=world_number * 100_000 + run_seed * 100 + 90,
        raw_spec=final_spec,
        judge=judge,
        cache_dir=cache_dir / "tier-b",
        batch_pairs=judge_batch_pairs,
        concurrency=judge_concurrency,
        expected=True,
    )
    baseline_results = []
    for iteration in range(1, iterations + 1):
        baseline_spec = {**first_spec, "iteration": iteration, "policy": "frozen-v0"}
        baseline_results.append(
            simulate(
                world_number=world_number,
                seed=world_number * 100_000 + run_seed * 100 + iteration,
                raw_spec=baseline_spec,
                judge=judge,
                cache_dir=cache_dir / "tier-b",
                batch_pairs=judge_batch_pairs,
                concurrency=judge_concurrency,
            )
        )
    summary = {
        "schema": "takyon.tier-b-experiment.v2",
        "world": world_number,
        "run_seed": run_seed,
        "iterations": iterations,
        "budget_per_iteration": budget,
        "goal": goal,
        "schedule_name": schedule_name,
        "schedule": schedule.record(),
        "loop": {
            "spend": sum(record["result"]["spend"] for record in history),
            "revenue": sum(record["result"]["revenue"] for record in history),
            "mean_roas": sum(record["result"]["roas"] for record in history) / len(history),
            "aggregate_roas": (
                sum(record["result"]["revenue"] for record in history)
                / sum(record["result"]["spend"] for record in history)
            ),
            "roas_by_iteration": [record["result"]["roas"] for record in history],
            "final_policy_version": policy_version,
        },
        "frozen_baseline": {
            "spend": sum(result["spend"] for result in baseline_results),
            "revenue": sum(result["revenue"] for result in baseline_results),
            "mean_roas": sum(result["roas"] for result in baseline_results) / len(baseline_results),
            "aggregate_roas": (
                sum(result["revenue"] for result in baseline_results)
                / sum(result["spend"] for result in baseline_results)
            ),
            "roas_by_iteration": [result["roas"] for result in baseline_results],
        },
        "held_out_expected": {
            "initial_roas": initial_expected["roas"],
            "final_roas": final_expected["roas"],
            "change": final_expected["roas"] - initial_expected["roas"],
            "ratio": (
                final_expected["roas"] / initial_expected["roas"]
                if initial_expected["roas"]
                else None
            ),
        },
        "selected_rungs": [entry["selected_rung"] for entry in lineage],
        "judge": {
            "provider": judge.config.provider,
            "model": judge.config.model or "default",
            "identity": judge.config.identity,
            "stats": judge.stats.record(),
        },
        "agent": {
            "provider": agent.config.provider,
            "model": agent.config.model or "default",
            "identity": agent.config.identity,
            "stats": agent.stats.record(),
        },
        "output_dir": str(output_dir),
    }
    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "final-evaluation-spec.json", final_spec)
    _write_json(output_dir / "initial-expected.json", initial_expected)
    _write_json(output_dir / "final-expected.json", final_expected)
    _write_json(output_dir / "baseline.json", baseline_results)
    return summary


def _config(prefix: str, args: argparse.Namespace) -> LLMConfig:
    return LLMConfig(
        provider=getattr(args, f"{prefix}_provider"),
        model=getattr(args, f"{prefix}_model"),
        base_url=getattr(args, f"{prefix}_base_url"),
        api_key_env=getattr(args, f"{prefix}_api_key_env"),
        timeout_seconds=args.timeout,
        max_output_tokens=24000 if prefix == "agent" else 12000,
        temperature=0.2,
        codex_bin=args.codex_bin,
    )


def _add_llm_args(parser: argparse.ArgumentParser, prefix: str) -> None:
    parser.add_argument(f"--{prefix}-provider", choices=("codex", "openai"), default="codex")
    parser.add_argument(f"--{prefix}-model", default="")
    parser.add_argument(f"--{prefix}-base-url", default="")
    parser.add_argument(f"--{prefix}-api-key-env", default=f"TIER_B_{prefix.upper()}_API_KEY")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", type=int)
    parser.add_argument("--landing-page", type=Path, required=True)
    parser.add_argument("--seed-policy", type=Path, default=SIM_ROOT / "seed-policy.md")
    parser.add_argument("--goal", default="maximize settled purchase ROAS")
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--budget", type=float, default=200.0)
    parser.add_argument("--run-seed", type=int, default=1)
    parser.add_argument("--schedule", choices=tuple(PRESETS), default="default")
    parser.add_argument("--tau0", type=float)
    parser.add_argument("--decay", type=float)
    parser.add_argument("--floor", type=float)
    parser.add_argument("--width", type=float)
    parser.add_argument("--pair-scale", type=float)
    parser.add_argument("--pattern-scale", type=float)
    parser.add_argument("--design-scale", type=float)
    parser.add_argument("--judge-batch-pairs", type=int, default=10)
    parser.add_argument("--judge-concurrency", type=int, default=3)
    parser.add_argument("--cache-dir", type=Path, default=SIM_ROOT / "cache")
    parser.add_argument("--output-root", type=Path, default=SIM_ROOT / "runs" / "tier-b")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--codex-bin", default="")
    _add_llm_args(parser, "judge")
    _add_llm_args(parser, "agent")
    args = parser.parse_args(argv)
    try:
        landing_page = args.landing_page.read_text(encoding="utf-8").strip()
        seed_policy = args.seed_policy.read_text(encoding="utf-8").strip()
        base = PRESETS[args.schedule]
        schedule = NoiseSchedule(
            tau0=args.tau0 if args.tau0 is not None else base.tau0,
            decay=args.decay if args.decay is not None else base.decay,
            floor=args.floor if args.floor is not None else base.floor,
            width=args.width if args.width is not None else base.width,
            rungs=base.rungs,
            pair_scale=args.pair_scale if args.pair_scale is not None else base.pair_scale,
            pattern_scale=(
                args.pattern_scale if args.pattern_scale is not None else base.pattern_scale
            ),
            design_scale=(
                args.design_scale if args.design_scale is not None else base.design_scale
            ),
        )
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = _safe_run_name(f"world-{args.world}-{args.schedule}-seed-{args.run_seed}-{stamp}")
        output_dir = args.output_root / name
        response_cache = args.cache_dir / "llm"
        judge_config = _config("judge", args)
        agent_config = _config("agent", args)
        if not judge_config.model or not agent_config.model:
            raise ExperimentError(
                "full experiments require explicit --judge-model and --agent-model so caches "
                "and results cannot silently change with a provider default"
            )
        judge = StructuredLLM(judge_config, response_cache_dir=response_cache)
        agent = StructuredLLM(agent_config, response_cache_dir=response_cache)
        summary = run_experiment(
            world_number=args.world,
            run_seed=args.run_seed,
            iterations=args.iterations,
            budget=args.budget,
            goal=args.goal,
            landing_page=landing_page,
            seed_policy=seed_policy,
            schedule=schedule,
            schedule_name=args.schedule,
            judge=judge,
            agent=agent,
            cache_dir=args.cache_dir,
            output_dir=output_dir,
            judge_batch_pairs=args.judge_batch_pairs,
            judge_concurrency=args.judge_concurrency,
        )
    except (
        OSError,
        json.JSONDecodeError,
        LLMError,
        TierBError,
        ExperimentError,
        ValueError,
    ) as exc:
        parser.exit(2, f"tier-b experiment failed: {exc}\n")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
