"""Spend boundary for paid web-provider egress (search / extract / crawl) and their summarizer LLM.

This is the seam Codex specified: the core web tools resolve the ACTUAL provider that will run, and
— for a provider that spends platform money — reserve here BEFORE the call and settle/release AFTER.
The only money that ever leaves does so through a budget reservation taken at the egress point, so
there is no "metered near the spend" guesswork (the brittleness of the old pre/post tool-call hooks,
which fired before the provider was chosen).

Trust split:
- Untrusted request inputs (query, urls, depth) NEVER reach this module. Only the server-resolved
  provider name, the server-owned `pricing_key`, and server-computed `units`/`usage` do.
- The reserve/settle/release IMPLEMENTATION is registered by the runtime (Takyon registers a
  business-budget meter via :func:`register_spend_meter`). Core ships no implementation, so a plain
  non-Takyon Hermes runtime is unmetered — `reserve_paid_call` returns ``None`` (allow) when there is
  no billing context.

Fail-closed invariants:
- A paid call in a business session with no meter registered → :class:`SpendBlocked` (never spend).
- An over-budget / unpriced / unknown-billing reservation → the meter raises (propagates), and since
  the reservation happens before the provider call, nothing is spent.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class SpendBlocked(Exception):
    """A paid web call was refused BEFORE egress: over budget, unpriced/unknown billing class, or a
    paid call inside a business session with no spend meter registered. Callers must surface this as
    a tool error and must NOT perform the provider call."""


@runtime_checkable
class SpendMeter(Protocol):
    """Runtime-registered budget implementation. ``reserve`` returns an opaque handle (or ``None``
    when there is genuinely nothing to meter, e.g. a dev runtime without a budget store); ``settle``
    and ``release`` receive that handle back."""

    def reserve(self, *, pricing_key, provider, op, units, usage, purpose) -> Any: ...

    def settle(self, handle: Any, *, units, usage) -> None: ...

    def release(self, handle: Any, *, error) -> None: ...


_meter: Optional[SpendMeter] = None


def register_spend_meter(meter: Optional[SpendMeter]) -> None:
    """Install (or clear, with ``None``) the runtime spend meter. Last writer wins."""
    global _meter
    _meter = meter


def get_spend_meter() -> Optional[SpendMeter]:
    return _meter


def _meter_required() -> bool:
    """True when we are operating inside a business scope, so money MUST be metered. Read from the
    SAME trusted session signal the Takyon runtime sets (``TAKYON_SESSION_BUSINESS_SLUG``); its
    presence means a meter is mandatory and a paid call without one fails closed. This is only a
    runtime env-var convention — no import of the Takyon plugin from core."""
    try:
        from gateway import session_context  # core module; safe to import here

        value = session_context.get_session_env("TAKYON_SESSION_BUSINESS_SLUG")
        if value and str(value).strip():
            return True
    except Exception:
        pass
    return bool(os.environ.get("TAKYON_SESSION_BUSINESS_SLUG", "").strip())


@dataclass
class _PaidCallHandle:
    """Binds a reservation to the meter that created it, so settle/release can't drift to a meter
    that was swapped mid-call."""

    meter: SpendMeter
    inner: Any


def reserve_paid_call(
    *,
    pricing_key,
    provider: str,
    op: str,
    units: int = 1,
    usage: Any = None,
    purpose: str,
) -> Optional[_PaidCallHandle]:
    """Reserve budget for a paid provider call at the egress point. Returns a handle to thread into
    :func:`settle_paid_call` / :func:`release_paid_call`, or ``None`` when there is no billing
    context (non-Takyon runtime, or global/non-business scope). Raises :class:`SpendBlocked` when a
    paid call must be refused. Any other meter error (e.g. DB failure) propagates — fail closed,
    because the reservation precedes the provider call."""
    meter = _meter
    if meter is None:
        if _meter_required():
            raise SpendBlocked(
                f"{op} via {provider} spends real money but no spend meter is registered in this "
                "business session; refusing ungated provider spend"
            )
        return None  # no billing context — non-Takyon / global scope is unmetered by design
    inner = meter.reserve(
        pricing_key=pricing_key,
        provider=provider,
        op=op,
        units=units,
        usage=usage,
        purpose=purpose,
    )
    return _PaidCallHandle(meter, inner)


def settle_paid_call(handle: Optional[_PaidCallHandle], *, units: Any = None, usage: Any = None) -> None:
    """Finalize a reservation at the real spend. No-op for a ``None`` handle. Best-effort: a settle
    failure leaves the hold in place (an over-hold of the estimate — conservative, never an
    under-charge), recoverable by reconciliation."""
    if handle is None:
        return
    try:
        handle.meter.settle(handle.inner, units=units, usage=usage)
    except Exception:  # pragma: no cover - defensive
        logger.warning("settle_paid_call failed; reservation left held (conservative)", exc_info=True)


def release_paid_call(handle: Optional[_PaidCallHandle], *, error: Any = None) -> None:
    """Release a reservation without recording spend (the no-spend / failure path). No-op for a
    ``None`` handle; best-effort, same conservative posture as :func:`settle_paid_call`."""
    if handle is None:
        return
    try:
        handle.meter.release(handle.inner, error=error)
    except Exception:  # pragma: no cover - defensive
        logger.warning("release_paid_call failed; reservation left held (conservative)", exc_info=True)
