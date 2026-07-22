"""Compare one-shot blind and fully informed ad policies in the same market batch."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
from typing import Any, Mapping

try:
    from .full_info_policy_v2 import _normalize, _schema, _write_json
    from .llm_client import LLMConfig, StructuredLLM
    from .population_market_v2 import (
        CHOICE_CALIBRATION_PATH,
        MODEL_PATH,
        SIM_ROOT,
        evaluate_policies,
        load_choice_calibration,
        load_population_model,
        load_variation_model,
        public_receipt,
        sampled_replay,
    )
    from .tier_b_market import _load_world
except ImportError:  # pragma: no cover
    from full_info_policy_v2 import _normalize, _schema, _write_json
    from llm_client import LLMConfig, StructuredLLM
    from population_market_v2 import (
        CHOICE_CALIBRATION_PATH,
        MODEL_PATH,
        SIM_ROOT,
        evaluate_policies,
        load_choice_calibration,
        load_population_model,
        load_variation_model,
        public_receipt,
        sampled_replay,
    )
    from tier_b_market import _load_world


def _designer_prompt(
    *, landing_page: str, platform: Mapping[str, Any], budget: float, periods: int,
    product_name: str, price: float, offer: str, hidden: Mapping[str, Any] | None,
) -> str:
    information = (
        """You have no hidden audience composition, persona, response, calibration, or receipt
information. Do not assume any. Use ordinary advertising judgment and only the public product
and platform information below. Try your best."""
        if hidden is None
        else f"""You have complete simulator information below. Use it to maximize expected
settled first-payment ROAS. Try your best. Do not write evaluator instructions or unsupported
product claims.

<hidden_simulator_json>
{json.dumps(hidden, indent=2, ensure_ascii=False)}
</hidden_simulator_json>"""
    )
    return f"""Create exactly one complete Meta advertising policy for {product_name}.

The policy runs unchanged for {periods} periods at ${budget:.2f} per period. Return three
publication-ready timeline ads, one objective, one audience, and spend shares. Every ad must
receive 10%-70% of spend. Optimize actual creative and targeting. Show only capabilities and
offer terms in the landing page. The simulator credits ${price:.2f} per settled first payment;
the public offer is {offer}. Make every scene continuous from second zero through the declared
duration. Account for short mobile attention and purchase friction.

{information}

<public_platform_json>
{json.dumps(platform, indent=2, ensure_ascii=False)}
</public_platform_json>

<public_landing_page>
{landing_page}
</public_landing_page>
"""


def _materialize(raw: Any, *, label: str, landing_page: str, budget: float) -> dict[str, Any]:
    _, policies = _normalize(
        raw,
        generation=1,
        candidate_count=1,
        landing_page=landing_page,
        budget=budget,
    )
    policy = policies[0]
    old_ids = [ad["id"] for ad in policy["ads"]]
    new_ids = [f"{label}-ad-{index}" for index in range(1, 4)]
    id_map = dict(zip(old_ids, new_ids))
    for ad in policy["ads"]:
        ad["id"] = id_map[ad["id"]]
    policy["id"] = f"{label}-policy"
    policy["strategy"] = f"{label}: {policy['strategy']}"
    for campaign in policy["campaigns"]:
        campaign["id"] = f"{label}-campaign"
        campaign["ad_ids"] = [id_map[value] for value in campaign["ad_ids"]]
        campaign["ad_weights"] = {
            id_map[key]: value for key, value in campaign["ad_weights"].items()
        }
    return policy


def _metrics(result: Mapping[str, Any], samples: list[Mapping[str, Any]]) -> dict[str, Any]:
    scenarios = (result.get("subscription_roas_scenarios") or {}).get("scenarios") or {}
    reference = scenarios.get("reference_10pct") or {}
    month_12 = (reference.get("horizons") or {}).get("month_12") or {}
    sampled_roas = [float(sample["roas"]) for sample in samples]
    return {
        "policy_id": result["policy"]["id"],
        "first_payment_roas": result["first_payment_roas"],
        "first_payment_revenue": result["first_payment_revenue"],
        "purchases": result["funnel"]["purchases"],
        "spend": result["spend"],
        "twelve_month_revenue_roas_reference_10pct_churn": month_12.get("cohort_revenue_roas"),
        "twelve_month_contribution_roas_reference_10pct_churn": month_12.get("cohort_contribution_roas"),
        "sampled_first_payment_roas_mean": sum(sampled_roas) / len(sampled_roas),
        "sampled_first_payment_roas_min": min(sampled_roas),
        "sampled_first_payment_roas_max": max(sampled_roas),
    }


def run_comparison(
    *, world_number: int, landing_page: str, budget: float, periods: int, run_seed: int,
    designer_model: str, judge_model: str, judge_concurrency: int, sample_replays: int,
    model_path: Path, variation_path: Path, choice_calibration_path: Path,
    economics_path: Path, cache_dir: Path, output_dir: Path,
) -> dict[str, Any]:
    world, platform = _load_world(world_number)
    model = load_population_model(model_path)
    variation = load_variation_model(variation_path)
    choice = load_choice_calibration(choice_calibration_path)
    hidden = {
        "population_model": model,
        "human_variation": variation,
        "choice_calibration": choice,
    }
    blind_client = StructuredLLM(LLMConfig(
        provider="codex", model=designer_model, timeout_seconds=1200, max_output_tokens=12000,
    ), response_cache_dir=cache_dir / "blind-designer")
    full_client = StructuredLLM(LLMConfig(
        provider="codex", model=designer_model, timeout_seconds=1200, max_output_tokens=12000,
    ), response_cache_dir=cache_dir / "full-designer")
    prompts = {
        "blind": _designer_prompt(
            landing_page=landing_page, platform=platform, budget=budget, periods=periods,
            product_name=str(model.get("business_name") or "the advertised product"),
            price=float(world["price_usd"]), offer=str(world["offer"]), hidden=None,
        ),
        "full-info": _designer_prompt(
            landing_page=landing_page, platform=platform, budget=budget, periods=periods,
            product_name=str(model.get("business_name") or "the advertised product"),
            price=float(world["price_usd"]), offer=str(world["offer"]), hidden=hidden,
        ),
    }
    clients = {"blind": blind_client, "full-info": full_client}

    def design(label: str) -> tuple[str, Any]:
        return label, clients[label].complete(
            prompt=prompts[label],
            schema=_schema(1),
            cache_namespace=f"blind-vs-full-{label}-designer-v1",
        )

    raw_designs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(design, label) for label in ("blind", "full-info")]
        for future in concurrent.futures.as_completed(futures):
            label, raw = future.result()
            raw_designs[label] = raw
    policies = [
        _materialize(raw_designs[label], label=label, landing_page=landing_page, budget=budget)
        for label in ("blind", "full-info")
    ]
    judge = StructuredLLM(LLMConfig(
        provider="codex", model=judge_model, timeout_seconds=1200, max_output_tokens=20000,
    ), response_cache_dir=cache_dir / "judge")
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
    by_id = {result["policy"]["id"]: result for result in results}
    samples = {
        label: [
            sampled_replay(
                by_id[f"{label}-policy"],
                seed=world_number * 1_000_000 + run_seed * 10_000 + index,
            )
            for index in range(1, sample_replays + 1)
        ]
        for label in ("blind", "full-info")
    }
    metrics = {
        label: _metrics(by_id[f"{label}-policy"], samples[label])
        for label in ("blind", "full-info")
    }
    blind_roas = metrics["blind"]["first_payment_roas"]
    full_roas = metrics["full-info"]["first_payment_roas"]
    summary = {
        "schema": "takyon.blind-vs-full-info.v1",
        "world": world_number,
        "run_seed": run_seed,
        "budget_per_period": budget,
        "periods": periods,
        "designer_model": designer_model,
        "judge_model": judge_model,
        "comparison": metrics,
        "full_info_minus_blind_first_payment_roas": full_roas - blind_roas,
        "full_info_relative_lift": full_roas / blind_roas - 1.0 if blind_roas else None,
        "winner": "full-info" if full_roas > blind_roas else "blind",
        "designer_stats": {
            label: clients[label].stats.record() for label in ("blind", "full-info")
        },
        "judge_stats": judge.stats.record(),
        "output_dir": str(output_dir),
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_json(output_dir / "prompts.json", prompts)
    _write_json(output_dir / "raw-designs.json", raw_designs)
    _write_json(output_dir / "policies.json", policies)
    _write_json(output_dir / "public-results.json", [public_receipt(result) for result in results])
    _write_json(output_dir / "hidden-results.json", results)
    _write_json(output_dir / "sampled-replays.json", samples)
    _write_json(output_dir / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", type=int)
    parser.add_argument("--landing-page", type=Path, required=True)
    parser.add_argument("--budget", type=float, default=200.0)
    parser.add_argument("--periods", type=int, default=8)
    parser.add_argument("--run-seed", type=int, default=1)
    parser.add_argument("--designer-model", default="gpt-5.6-sol")
    parser.add_argument("--judge-model", default="gpt-5.6-luna")
    parser.add_argument("--judge-concurrency", type=int, default=5)
    parser.add_argument("--sample-replays", type=int, default=200)
    parser.add_argument("--population-model", type=Path, default=MODEL_PATH)
    parser.add_argument("--human-variation-model", type=Path, default=SIM_ROOT / "human-variation-v4.json")
    parser.add_argument("--choice-calibration", type=Path, default=CHOICE_CALIBRATION_PATH)
    parser.add_argument("--subscription-economics", type=Path, default=SIM_ROOT / "subscription-economics-v1.json")
    parser.add_argument("--cache-dir", type=Path, default=SIM_ROOT / "cache" / "blind-vs-full-info-v1")
    parser.add_argument("--output-root", type=Path, default=SIM_ROOT / "runs" / "blind-vs-full-info-v1")
    args = parser.parse_args(argv)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_root / f"world-{args.world}-seed-{args.run_seed}-{stamp}"
    summary = run_comparison(
        world_number=args.world,
        landing_page=args.landing_page.read_text(encoding="utf-8"),
        budget=args.budget,
        periods=args.periods,
        run_seed=args.run_seed,
        designer_model=args.designer_model,
        judge_model=args.judge_model,
        judge_concurrency=args.judge_concurrency,
        sample_replays=args.sample_replays,
        model_path=args.population_model,
        variation_path=args.human_variation_model,
        choice_calibration_path=args.choice_calibration,
        economics_path=args.subscription_economics,
        cache_dir=args.cache_dir,
        output_dir=output_dir,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
