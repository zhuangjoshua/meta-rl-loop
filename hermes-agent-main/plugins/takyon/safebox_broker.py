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
    or BrokerError (replay). `nonce_store` needs `.claim(nonce, expires_at, now=...) -> bool`."""
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
) -> Any:
    """The full brokered provider call, entirely inside the safebox process / host:

      verify token -> claim nonce -> RESERVE budget keyed on the validated {business, app_user}
      -> resolve the provider key LOCALLY -> call the provider -> SETTLE actual (or RELEASE on
      failure) -> return a KEY-FREE result.

    The provider key is resolved and used only inside `key_resolver`/`provider_caller` here on the
    safebox; it never enters the response. Spend is reserved before the call against the AUTHORITATIVE
    scope, so a caller can neither forge usage nor see the key. `ledger` needs
    `.reserve(scope, estimate)->reservation`, `.settle(reservation, actual)`, `.release(reservation)`;
    `provider_caller` returns `(key_free_result, actual_microusd)`.
    """

    def execute(scope: CapabilityScope) -> Any:
        est = int(estimate_microusd)
        if int(scope.max_cost_microusd) and est > int(scope.max_cost_microusd):
            raise BrokerError("estimate_exceeds_ceiling")
        reservation = ledger.reserve(scope, est)  # raises (e.g. AppBudgetExceeded) on insufficient funds
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

    return broker_call(
        token=token,
        signing_key=signing_key,
        expected_audience=audience,
        now=now,
        nonce_store=nonce_store,
        execute=execute,
    )
