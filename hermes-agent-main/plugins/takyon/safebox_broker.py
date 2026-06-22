"""Safebox broker core (Phase 1/2 of deploy/SAFEBOX-BROKER-REMEDIATION-PLAN.md).

This module is NEW code that does not run until Codex wires the broker routes into the safebox app and
deletes the raw-key egress + client raw paths (the cutover). There is NO permanent flag/fallback: once
clients call the broker, the unsafe `/v1/env/*` egress and in-client provider calls are removed, so the
broker is the ONLY path that can reach a provider key or spend.

`broker_call` is the single chokepoint every paid provider call goes through:
  1. verify the capability token (signature + audience + expiry) -> the AUTHORITATIVE scope;
  2. claim its nonce exactly once (single-use -> no replay);
  3. delegate to `execute(scope)`, which the safebox route supplies and which — INSIDE the safebox
     process, on the safebox host — reserves budget keyed on the validated {business, app_user},
     resolves the provider key, calls the provider, settles, and returns a KEY-FREE result.

Because the scope is verified (not client-asserted) and the key/reserve live inside `execute` on the
safebox, a caller can neither forge usage, spend cross-tenant, nor see the key. This module holds only
the verify+single-use+delegate logic so it is unit-testable without a live provider or DB.
"""
from __future__ import annotations

from typing import Any, Callable

from .safebox_capability import CapabilityScope, verify_capability


class BrokerError(Exception):
    """The brokered call was refused (replayed token, or execute() failed closed)."""


def broker_call(
    *,
    token: str,
    signing_key: bytes,
    expected_audience: str,
    now: int,
    nonce_store: Any,
    execute: Callable[[CapabilityScope], Any],
) -> Any:
    """Verify -> claim nonce -> execute(scope). Raises CapabilityError (bad/expired/wrong-audience token)
    or BrokerError (replay). `nonce_store` needs `.claim(nonce, expires_at, now=...) -> bool`.

    NOTE: the full provider path (`handle_provider_request`) does NOT route its reserve/settle through
    this helper — it must reserve BEFORE the single irreversible nonce claim so a refused/transient
    reserve does not burn a pre-minted token. This helper stays for the bare verify->claim->execute
    shape used by callers that have nothing to reserve before the claim."""
    scope, nonce, exp = verify_capability(
        token, signing_key=signing_key, expected_audience=expected_audience, now=now
    )
    if not nonce_store.claim(nonce, exp, now=now):
        raise BrokerError("replayed_token")
    return execute(scope)


def handle_provider_request(
    *,
    token: str,
    signing_key: bytes,
    audience: str,
    now: int,
    nonce_store: Any,
    ledger: Any,
    key_resolver: Callable[[CapabilityScope], str],
    provider_caller: Callable[[CapabilityScope, str], tuple[Any, int]],
    estimate_microusd: int,
    estimate_fn: Callable[[CapabilityScope], int] | None = None,
) -> Any:
    """The full brokered provider call, entirely inside the safebox process / host:

      verify token -> ceiling-check -> RESERVE budget keyed on the validated {business, app_user}
      -> claim nonce (single-use, BEFORE the irreversible provider call) -> resolve the provider key
      LOCALLY -> call the provider -> SETTLE actual (or RELEASE on failure) -> return a KEY-FREE result.

    The provider key is resolved and used only inside `key_resolver`/`provider_caller` here on the
    safebox; it never enters the response. Spend is reserved before the call against the AUTHORITATIVE
    scope, so a caller can neither forge usage nor see the key. `ledger` needs
    `.reserve(scope, estimate)->reservation`, `.settle(reservation, actual)`, `.release(reservation)`;
    `provider_caller` returns `(key_free_result, actual_microusd)`.

    `max_cost_microusd` on the scope is a HARD ceiling: the estimate gated against it is
    `max(server_estimate, client_estimate)`, where the server estimate (when `estimate_fn` is given)
    is computed from the provider's own pricing source so a client cannot pass a tiny estimate to duck
    the cap. An est above the ceiling is refused; a 0 ceiling means a 0 est only (a free action) — any
    positive est against a 0 ceiling is refused.

    Ordering (money + replay integrity): the ledger RESERVE happens BEFORE the nonce claim, so a
    refused or transient-failed reserve never burns a pre-minted single-use token (a retry can
    succeed). The nonce claim still precedes the irreversible provider call, so a replayed token that
    survives to here releases the just-made hold and refuses."""
    # 1. Verify the token -> the AUTHORITATIVE scope (signature + audience + expiry).
    scope, nonce, exp = verify_capability(
        token, signing_key=signing_key, expected_audience=audience, now=now
    )

    # 2. Ceiling-check the SERVER-floored estimate against the hard ceiling (mc==0 ⇒ only est==0 ok).
    server_estimate = int(estimate_fn(scope)) if estimate_fn else 0
    est = max(server_estimate, int(estimate_microusd))
    ceiling = int(scope.max_cost_microusd)
    if est > ceiling:
        raise BrokerError("estimate_exceeds_ceiling")

    # 3. Reserve BEFORE the single-use nonce claim (a refused/transient reserve must not burn the
    #    token). reserve() raises (e.g. AppBudgetExceeded) on insufficient funds.
    reservation = ledger.reserve(scope, est)

    # 4. Claim the nonce exactly once. A replay here means the token was already spent: release the
    #    hold we just made and refuse. This claim still precedes the irreversible provider call below.
    if not nonce_store.claim(nonce, exp, now=now):
        ledger.release(reservation)
        raise BrokerError("replayed_token")

    # 5. Resolve the key LOCALLY and call the provider; settle on success, release on any failure.
    try:
        key = key_resolver(scope)
        if not key:
            raise BrokerError("provider_key_unconfigured")
        result, actual = provider_caller(scope, key)
    except Exception:
        ledger.release(reservation)
        raise
    ledger.settle(reservation, int(actual))
    return result
