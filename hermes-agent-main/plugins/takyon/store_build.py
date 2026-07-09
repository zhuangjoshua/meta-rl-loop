"""EAS build-lane orchestration — the settle-at-trigger money boundary (readmodular §2.2/§7).

Pure, injectable, testable: `run_build` takes the creative-credit fns and the EAS invoker as
arguments so the MONEY-SAFETY can be proven without a real build (the part that needs money-correct
behavior) while the real `eas-cli` call (the part that needs the builder + Expo egress + the one-time
`eas credentials` login) is deferred behind a fail-closed invoker.

The one hard money rule: an EAS build spends real money the instant Expo ACCEPTS the upload/trigger,
NOT when the build finishes. So:
  * RESERVE creative credits before invoking EAS;
  * SETTLE at successful TRIGGER (build id returned) — the irreversible-spend point;
  * RELEASE only when the trigger provably did not happen (invoker raised before Expo accepted).
The follow-on poll/wait is a $0 step that must NEVER release the settled reservation.

Fail-closed: an unconfigured builder / missing custody / disabled archetype raises BEFORE reserve,
so nothing is charged. Idempotent on the reservation key (a retried publish never double-charges).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


class StoreBuildError(Exception):
    """Base for build-lane errors."""


class EasBuilderUnconfigured(StoreBuildError):
    """The jailed EAS builder (docker sandbox + Expo egress) and/or the first `eas credentials` login
    is not provisioned yet — a REAL build cannot run. Carries the ``eas_builder_unconfigured`` gate
    token. Raised by the default invoker BEFORE any credit reserve, so nothing is charged; the CEO's
    discovery surface is the error itself."""


@dataclass(frozen=True)
class BuildResult:
    build_id: str
    lane: str
    actual_credits: Optional[int] = None
    detail: str = ""
    # expo.dev build page (status + full build logs) — carried into the settle metadata and the
    # tool receipt so the repair loop can fetch failure logs without reconstructing the URL.
    logs_url: str = ""


def run_build(
    *,
    business_slug: str,
    lane: str,
    credits: int,
    reservation_key: str,
    reserve: Callable[[str, int, str], Any],
    settle: Callable[[str, Optional[int], dict], Any],
    release: Callable[[str, dict], Any],
    invoke_eas: Callable[[], BuildResult],
) -> BuildResult:
    """Orchestrate one EAS build with the settle-at-trigger boundary.

    reserve(business_slug, credits, reservation_key) -> reservation (before the provider call)
    invoke_eas() -> BuildResult on a successful TRIGGER, or raises (before Expo accepted → no spend)
    settle(reservation_key, actual_credits, metadata)  (called ONLY on successful trigger)
    release(reservation_key, metadata)                 (called ONLY when the trigger did not happen)

    Returns the BuildResult. Re-raises the invoker's error after releasing.
    """
    if credits < 0:
        raise StoreBuildError("credits must be >= 0")
    # Reserve first — a build must never run without covered credits (fail-closed on insufficient).
    reserve(business_slug, credits, reservation_key)
    try:
        result = invoke_eas()
    except Exception as exc:
        # The trigger did not happen (or failed before Expo accepted) → no money spent → release.
        release(reservation_key, {"error": str(exc)[:300], "lane": lane})
        raise
    # Expo accepted the build (build id returned) → money is spent → SETTLE now, not at poll time.
    settle(
        reservation_key,
        result.actual_credits if result.actual_credits is not None else credits,
        {
            "build_id": result.build_id,
            "lane": lane,
            **({"logs_url": result.logs_url} if getattr(result, "logs_url", "") else {}),
        },
    )
    return result


def default_eas_invoker(*, business_slug: str, lane: str, expo_token: str) -> Callable[[], BuildResult]:
    """The real-build invoker — fail-closed until the jailed builder + Expo egress + the one-time
    `eas credentials` login are provisioned. Returns a thunk (so run_build controls the money order).

    Today it raises ``EasBuilderUnconfigured`` (the builder droplet/egress allowlist and the first
    interactive `eas credentials` Apple-ID login are not set up), so a publish reserves nothing and
    fails closed with a clear next step. When the builder lands, this thunk will: hydrate the pinned
    Expo source into the jailed docker sandbox, `npm ci --ignore-scripts`, run `eas build
    --non-interactive --profile <lane>` with the safebox-minted EXPO_TOKEN, and return the build id +
    actual credits from the EAS API — never touching a Takyon-host env."""

    def _thunk() -> BuildResult:
        raise EasBuilderUnconfigured(
            "eas_builder_unconfigured: the jailed EAS builder (docker sandbox + Expo-egress allowlist) "
            "and the one-time `eas credentials` Apple-ID login are not provisioned yet, so a real "
            "build cannot run. The credential is in custody (EXPO_TOKEN resolves), but signing needs "
            "the interactive `eas credentials` login. No credits were reserved."
        )

    return _thunk
