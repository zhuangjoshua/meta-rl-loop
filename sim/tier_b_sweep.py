"""Run paired Tier-B schedule comparisons, parallelized across hidden worlds.

Runs for the same world are serialized so their hidden-judgment cache cannot be
concurrently rewritten. Different worlds may run in parallel. Any failed experiment
fails the sweep; partial success is retained in the individual run directories.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from .noise_schedule import PRESETS
    from .tier_b_market import SIM_ROOT
except ImportError:  # pragma: no cover - direct-script path
    from noise_schedule import PRESETS
    from tier_b_market import SIM_ROOT


class SweepError(RuntimeError):
    pass


def _csv_ints(value: str) -> list[int]:
    try:
        result = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not result:
        raise argparse.ArgumentTypeError("at least one world is required")
    return result


def _csv_strings(value: str) -> list[str]:
    result = [item.strip() for item in value.split(",") if item.strip()]
    if not result:
        raise argparse.ArgumentTypeError("at least one value is required")
    return result


def _aggregate(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    by_schedule: dict[str, list[dict[str, Any]]] = {}
    for summary in summaries:
        by_schedule.setdefault(summary["schedule_name"], []).append(summary)
    output = {}
    for name, runs in sorted(by_schedule.items()):
        held_out_changes = [run["held_out_expected"]["change"] for run in runs]
        output[name] = {
            "runs": len(runs),
            "mean_loop_revenue": statistics.fmean(run["loop"]["revenue"] for run in runs),
            "mean_baseline_revenue": statistics.fmean(
                run["frozen_baseline"]["revenue"] for run in runs
            ),
            "loop_revenue_wins": sum(
                run["loop"]["revenue"] > run["frozen_baseline"]["revenue"] for run in runs
            ),
            "mean_held_out_roas_change": statistics.fmean(held_out_changes),
            "held_out_improvement_wins": sum(change > 0 for change in held_out_changes),
            "mean_selected_rung": statistics.fmean(
                rung for run in runs for rung in run["selected_rungs"]
            ),
            "llm_calls": sum(
                run["judge"]["stats"]["calls"] + run["agent"]["stats"]["calls"]
                for run in runs
            ),
        }
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worlds", type=_csv_ints, required=True)
    parser.add_argument(
        "--schedules",
        type=_csv_strings,
        default=["default", "conservative"],
    )
    parser.add_argument("--replicates", type=int, default=1)
    parser.add_argument("--parallel-worlds", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--budget", type=float, default=200.0)
    parser.add_argument("--landing-page", type=Path, required=True)
    parser.add_argument("--seed-policy", type=Path, default=SIM_ROOT / "seed-policy.md")
    parser.add_argument("--goal", default="maximize settled purchase ROAS")
    parser.add_argument("--judge-provider", choices=("codex", "openai"), default="codex")
    parser.add_argument("--judge-model", required=True)
    parser.add_argument("--judge-base-url", default="")
    parser.add_argument("--judge-api-key-env", default="TIER_B_JUDGE_API_KEY")
    parser.add_argument("--agent-provider", choices=("codex", "openai"), default="codex")
    parser.add_argument("--agent-model", required=True)
    parser.add_argument("--agent-base-url", default="")
    parser.add_argument("--agent-api-key-env", default="TIER_B_AGENT_API_KEY")
    parser.add_argument("--judge-batch-pairs", type=int, default=10)
    parser.add_argument("--judge-concurrency", type=int, default=3)
    parser.add_argument("--cache-dir", type=Path, default=SIM_ROOT / "cache")
    parser.add_argument("--output-root", type=Path, default=SIM_ROOT / "runs" / "tier-b")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--codex-bin", default="")
    args = parser.parse_args(argv)
    unknown = [name for name in args.schedules if name not in PRESETS]
    if unknown:
        parser.error(f"unknown schedules: {', '.join(unknown)}")
    if args.replicates < 1 or args.parallel_worlds < 1:
        parser.error("replicates and parallel-worlds must be positive")

    experiment = SIM_ROOT / "tier_b_experiment.py"

    def run_world(world: int) -> list[dict[str, Any]]:
        completed = []
        for schedule in args.schedules:
            for replicate in range(1, args.replicates + 1):
                command = [
                    sys.executable,
                    str(experiment),
                    str(world),
                    "--landing-page",
                    str(args.landing_page),
                    "--seed-policy",
                    str(args.seed_policy),
                    "--goal",
                    args.goal,
                    "--iterations",
                    str(args.iterations),
                    "--budget",
                    str(args.budget),
                    "--run-seed",
                    str(replicate),
                    "--schedule",
                    schedule,
                    "--judge-provider",
                    args.judge_provider,
                    "--judge-model",
                    args.judge_model,
                    "--judge-api-key-env",
                    args.judge_api_key_env,
                    "--agent-provider",
                    args.agent_provider,
                    "--agent-model",
                    args.agent_model,
                    "--agent-api-key-env",
                    args.agent_api_key_env,
                    "--judge-batch-pairs",
                    str(args.judge_batch_pairs),
                    "--judge-concurrency",
                    str(args.judge_concurrency),
                    "--cache-dir",
                    str(args.cache_dir),
                    "--output-root",
                    str(args.output_root),
                    "--timeout",
                    str(args.timeout),
                ]
                if args.judge_base_url:
                    command.extend(["--judge-base-url", args.judge_base_url])
                if args.agent_base_url:
                    command.extend(["--agent-base-url", args.agent_base_url])
                if args.codex_bin:
                    command.extend(["--codex-bin", args.codex_bin])
                result = subprocess.run(
                    command,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                if result.returncode != 0:
                    raise SweepError(
                        f"world {world} schedule {schedule} replicate {replicate} failed: "
                        f"{(result.stderr or result.stdout)[-3000:]}"
                    )
                completed.append(json.loads(result.stdout))
        return completed

    summaries: list[dict[str, Any]] = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel_worlds) as executor:
            futures = {executor.submit(run_world, world): world for world in args.worlds}
            for future in concurrent.futures.as_completed(futures):
                summaries.extend(future.result())
    except (OSError, json.JSONDecodeError, SweepError) as exc:
        parser.exit(2, f"tier-b sweep failed: {exc}\n")

    result = {
        "schema": "takyon.tier-b-sweep.v1",
        "worlds": args.worlds,
        "schedules": args.schedules,
        "replicates": args.replicates,
        "iterations": args.iterations,
        "aggregate": _aggregate(summaries),
        "runs": summaries,
    }
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.output_root.mkdir(parents=True, exist_ok=True)
    output_path = args.output_root / f"sweep-{stamp}.json"
    temporary = output_path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, output_path)
    print(json.dumps({"output": str(output_path), **result["aggregate"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
