"""Seeded semantic-step noise schedules and schedule comparison utilities.

The semantic gradient supplies an ordered ladder:

    0 = keep incumbent, 1..K = smallest..boldest policy revision

Evidence chooses the direction of change.  This module chooses only its magnitude.
It is deliberately independent of the market and the LLM so schedules can be paired,
replayed, and swept without changing either source of evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class NoiseSchedule:
    tau0: float = 1.0
    decay: float = 0.92
    floor: float = 0.05
    width: float = 0.18
    rungs: int = 6
    pair_scale: float = 1.0
    pattern_scale: float = 1.0
    design_scale: float = 1.0

    def __post_init__(self) -> None:
        if self.tau0 <= 0:
            raise ValueError("tau0 must be positive")
        if not 0 < self.decay <= 1:
            raise ValueError("decay must be in (0, 1]")
        if not 0 <= self.floor < self.tau0:
            raise ValueError("floor must be in [0, tau0)")
        if self.width <= 0:
            raise ValueError("width must be positive")
        if self.rungs < 1:
            raise ValueError("rungs must be at least 1")
        if any(
            not 0 <= value <= 1
            for value in (self.pair_scale, self.pattern_scale, self.design_scale)
        ):
            raise ValueError("thesis-class scales must be in [0, 1]")

    def tau(self, iteration: int) -> float:
        if iteration < 0:
            raise ValueError("iteration must be non-negative")
        return max(self.floor, self.tau0 * self.decay**iteration)

    def target01(self, iteration: int) -> float:
        return (self.tau(iteration) - self.floor) / (self.tau0 - self.floor)

    def class_scale(self, thesis_class: str = "") -> float:
        if not thesis_class:
            return 1.0
        if thesis_class not in {"pair", "pattern", "design"}:
            raise ValueError("thesis_class must be pair, pattern, or design")
        return float(getattr(self, f"{thesis_class}_scale"))

    def probabilities(self, iteration: int, thesis_class: str = "") -> list[float]:
        target = self.target01(iteration) * self.class_scale(thesis_class)
        weights = [
            math.exp(-((rung / self.rungs) - target) ** 2 / (2 * self.width**2))
            for rung in range(self.rungs + 1)
        ]
        total = sum(weights)
        return [weight / total for weight in weights]

    def draw(self, iteration: int, seed: int, thesis_class: str = "") -> int:
        rng = random.Random(seed)
        pick = rng.random()
        cumulative = 0.0
        for rung, probability in enumerate(self.probabilities(iteration, thesis_class)):
            cumulative += probability
            if pick <= cumulative:
                return rung
        return self.rungs

    def expected_rung(self, iteration: int, thesis_class: str = "") -> float:
        return sum(
            i * p for i, p in enumerate(self.probabilities(iteration, thesis_class))
        )

    def record(self) -> dict[str, float | int]:
        return asdict(self)


PRESETS: dict[str, NoiseSchedule] = {
    "default": NoiseSchedule(decay=0.92, width=0.18),
    "conservative": NoiseSchedule(decay=0.82, width=0.12),
    "wide": NoiseSchedule(decay=0.92, width=0.30),
    "persistent": NoiseSchedule(decay=0.97, width=0.18),
    "greedy-small": NoiseSchedule(decay=0.25, floor=0.05, width=0.10),
    "semantic-gated": NoiseSchedule(
        decay=0.92,
        width=0.18,
        pair_scale=1.0,
        pattern_scale=0.25,
        design_scale=0.15,
    ),
}


def comparison_rows(
    schedules: Iterable[tuple[str, NoiseSchedule]], iterations: int, thesis_class: str = ""
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name, schedule in schedules:
        rows.append(
            {
                "name": name,
                "schedule": schedule.record(),
                "thesis_class": thesis_class or None,
                "iterations": [
                    {
                        "iteration": iteration,
                        "expected_rung": round(
                            schedule.expected_rung(iteration, thesis_class), 4
                        ),
                        "keep_probability": round(
                            schedule.probabilities(iteration, thesis_class)[0], 6
                        ),
                        "boldest_probability": round(
                            schedule.probabilities(iteration, thesis_class)[-1], 6
                        ),
                        "probabilities": [
                            round(value, 6)
                            for value in schedule.probabilities(iteration, thesis_class)
                        ],
                    }
                    for iteration in range(1, iterations + 1)
                ],
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument(
        "--presets",
        default=",".join(PRESETS),
        help="comma-separated schedule presets",
    )
    parser.add_argument("--thesis-class", choices=("pair", "pattern", "design"), default="")
    args = parser.parse_args(argv)
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    names = [name.strip() for name in args.presets.split(",") if name.strip()]
    unknown = [name for name in names if name not in PRESETS]
    if unknown:
        parser.error(f"unknown presets: {', '.join(unknown)}")
    print(
        json.dumps(
            comparison_rows(
                ((name, PRESETS[name]) for name in names),
                args.iterations,
                args.thesis_class,
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
