"""Per-business MONEY-SHAPE record + the shape gate + the minimal operator-approval affordance
(UC4 keystone, modularization plan §2.7 — the money-shape gate item).

The Roomier incident is the exact hole this closes: an autonomous/chat CEO turn minted a `one_time`
"credit-pack" plan for a business whose money shape is a recurring SUBSCRIPTION. The structural wake
ban (`core._refuse_on_autonomous_wake`) fires only on `task_kind == 'ceo_wake'`, so chat and bootstrap
turns wrote plans with zero money-shape validation. This module supplies the SEMANTIC gate that fires
at the plan-write choke point on EVERY task kind (chat, bootstrap, wake) — a subscription business can
never have a credit-pack/one_time plan minted, and a shape CHANGE requires an explicit operator
approval, not a silent flip.

Three money shapes (the archetype registry's money-shape axis — archetypes §3.3; UC4 owns the minimal
record now, the registry subsumes it later so `money_shape` becomes a derived attribute of
`businesses.archetype`):

  * ``subscription``    — recurring monthly plans (`app_plan_policies` rows). THE DEFAULT: an
                          undeclared business (money_shape NULL) reads as subscription, so every
                          existing business keeps behaving exactly as today.
  * ``credit_packs``    — fixed-price credit grants (the shape the Roomier `one_time` plan wanted).
  * ``cogs_passthrough``— per-order physical commerce funded by the customer's payment (orders shape).

House style (matches app_entitlements.py / app_identity.py / billing.py): a pure leaf that takes a
psycopg connection, imports no psycopg, opens its own ``conn.transaction()`` per mutating op, uses
``%s`` placeholders, and raises typed errors on broken preconditions. It has NO money authority of its
own — it declares and validates the shape; it never mints a plan, reserves credits, or touches a
ledger. The approval affordance is deliberately minimal and is the SEAM the archetypes plan (§1.5)
extends with more consumers (store submission, sample orders); it does not build a second table.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

# The known money shapes (kept in sync with the businesses_money_shape_chk migration constraint).
SUBSCRIPTION = "subscription"
CREDIT_PACKS = "credit_packs"
COGS_PASSTHROUGH = "cogs_passthrough"
MONEY_SHAPES = (SUBSCRIPTION, CREDIT_PACKS, COGS_PASSTHROUGH)

# The default shape for a business that has not declared one (money_shape NULL). A subuser plan
# (`app_plan_policies` row) is a recurring subscription by construction, so the default keeps every
# existing/undeclared business writing plans exactly as today.
DEFAULT_MONEY_SHAPE = SUBSCRIPTION

# The action_kind used for the one UC4 approval consumer: changing a business's declared money shape.
SHAPE_CHANGE_ACTION_KIND = "money_shape_change"

# Default TTL for a pending approval, in seconds (archetypes §1.5 "TTL-bounded"). One hour: long
# enough for an operator to click, short enough that a stale approval cannot silently authorize a
# later, different change.
DEFAULT_APPROVAL_TTL_SECONDS = 3600


class MoneyShapeError(Exception):
    """Base for money-shape / approval errors."""


class InvalidMoneyShape(MoneyShapeError):
    """A supplied shape is not one of the known MONEY_SHAPES."""


class MoneyShapeViolation(MoneyShapeError):
    """A money write (plan/credit-shape) does not match the business's declared money shape, and no
    approved shape change is present. This is the Roomier-hole refusal: it names the declared shape
    vs the attempted shape. Fires on EVERY task kind (chat, bootstrap, wake), unlike the wake ban."""


class ApprovalRequired(MoneyShapeViolation):
    """A money-SHAPE CHANGE was attempted without an approved operator-approval record. The message
    carries the exact `approval_required:<action_kind>` gate token so the CEO's discovery surface is
    the error itself (archetypes §1.5)."""


def normalize_money_shape(value, *, allow_empty: bool = True) -> str:
    """Normalize/validate a money-shape string. Empty/None → the default (subscription) when
    `allow_empty`, else raise. An unknown value always raises `InvalidMoneyShape`."""
    raw = str(value or "").strip().lower().replace("-", "_")
    if not raw:
        if allow_empty:
            return DEFAULT_MONEY_SHAPE
        raise InvalidMoneyShape("money_shape is required")
    if raw in {"subscription", "subscriptions", "sub", "recurring"}:
        return SUBSCRIPTION
    if raw in {"credit_packs", "credit_pack", "credits", "credit", "one_time", "credit_grant"}:
        return CREDIT_PACKS
    if raw in {"cogs_passthrough", "cogs", "passthrough", "orders", "order"}:
        return COGS_PASSTHROUGH
    raise InvalidMoneyShape(
        f"unknown money_shape {value!r}; must be one of {', '.join(MONEY_SHAPES)}"
    )


def payload_digest(payload) -> str:
    """Stable digest of an approval payload — the idempotency key. Deterministic (sorted keys),
    so re-requesting the SAME change returns the same pending record (idempotent on the digest)."""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── the per-business money-shape record ──────────────────────────────────────────────


def get_money_shape(conn, business_slug: str) -> str:
    """Read a business's DECLARED money shape, or the default (subscription) when undeclared/unknown.

    Pure read. Fails loud only if the business does not exist (so a plan write on a phantom business
    still trips the FK elsewhere; here we simply return the default rather than inventing a business)."""
    row = conn.execute(
        "select money_shape from businesses where slug = %s",
        (business_slug,),
    ).fetchone()
    if row is None:
        # Unknown business: the plan write itself will fail loud on the FK. Return the default so
        # this gate never masks that error with a shape violation.
        return DEFAULT_MONEY_SHAPE
    return normalize_money_shape(row[0], allow_empty=True)


def set_money_shape(
    conn,
    business_slug: str,
    money_shape: str,
    *,
    require_approval: bool = True,
    actor: str = "operator",
) -> str:
    """Declare (or change) a business's money shape. Validates the shape. When `require_approval`
    (the default for a CHANGE away from the current shape), an approved `money_shape_change` approval
    record for THIS target shape must be present and is CONSUMED atomically — a hallucinated
    "switch to credit packs" cannot flip the record silently.

    Setting the shape to the value it already holds is a no-op that never requires approval (declaring
    the default explicitly is free). Returns the persisted shape.
    """
    target = normalize_money_shape(money_shape, allow_empty=False)
    current = get_money_shape(conn, business_slug)
    if target == current:
        # Idempotent re-declaration of the same shape — never gated.
        with conn.transaction():
            conn.execute(
                "update businesses set money_shape = %s where slug = %s",
                (target, business_slug),
            )
        return target
    with conn.transaction():
        if require_approval:
            # Consume an approved shape-change approval for exactly this target, or fail closed.
            consume_approval(
                conn,
                business_slug,
                SHAPE_CHANGE_ACTION_KIND,
                {"from": current, "to": target},
            )
        conn.execute(
            "update businesses set money_shape = %s where slug = %s",
            (target, business_slug),
        )
    return target


# ── the gate (the choke point every plan/credit-shape write calls) ────────────────────


def assert_write_matches_shape(
    conn,
    business_slug: str,
    attempted_shape: str,
    *,
    task_kind: str = "",
) -> str:
    """The money-shape gate. Refuse a money write whose intent shape does not match the business's
    DECLARED shape. Fires on EVERY task kind — the caller passes `task_kind` only so the refusal
    message can name it; the gate does NOT relax for any kind (chat, bootstrap, and wake are all
    validated, unlike `_refuse_on_autonomous_wake` which fires only on ceo_wake).

    A `business_upsert_app_plan` write is a SUBSCRIPTION-shape write (an `app_plan_policies` row is a
    recurring monthly plan). So on a `subscription` business it passes; on a `credit_packs` or
    `cogs_passthrough` business it REFUSES with `MoneyShapeViolation` naming the declared vs attempted
    shape — the Roomier hole, closed on every path. Returns the declared shape when the write is
    allowed.

    A shape CHANGE is NOT performed here; declaring/changing the shape goes through `set_money_shape`
    (which requires an operator approval). This gate only validates that the write matches the shape
    already declared."""
    attempted = normalize_money_shape(attempted_shape, allow_empty=False)
    declared = get_money_shape(conn, business_slug)
    if attempted == declared:
        return declared
    kind = str(task_kind or "").strip().lower() or "chat"
    raise MoneyShapeViolation(
        f"money-shape mismatch on a {kind} turn: this business's declared money shape is "
        f"'{declared}', but the attempted write has money shape '{attempted}'. A '{declared}' "
        f"business cannot mint a '{attempted}' offer. This is refused on EVERY path (chat, bootstrap, "
        f"and wake), not only autonomous wakes. To change the business's money shape, request an "
        f"operator approval (action_kind='{SHAPE_CHANGE_ACTION_KIND}') and set the shape explicitly "
        f"first — the shape is never flipped silently by a plan write."
    )


# ── the minimal operator-approval affordance (archetypes §1.5; the seam) ──────────────


@dataclass(frozen=True)
class OperatorApproval:
    """One approval record. Idempotent on (business, action_kind, payload_digest); TTL-bounded via
    `expires_at`; single-consume via the status flip to 'consumed'; receipted via `receipt_path`."""

    id: str
    business_slug: str
    action_kind: str
    payload_digest: str
    status: str
    actor: str | None
    expires_at: object
    receipt_path: str | None
    metadata: dict


_APPROVAL_COLUMNS = (
    "id, business_slug, action_kind, payload_digest, status, actor, expires_at, receipt_path, metadata_json"
)


def _approval_from_row(row) -> OperatorApproval:
    return OperatorApproval(
        id=str(row[0]),
        business_slug=str(row[1]),
        action_kind=str(row[2]),
        payload_digest=str(row[3]),
        status=str(row[4]),
        actor=None if row[5] is None else str(row[5]),
        expires_at=row[6],
        receipt_path=None if row[7] is None else str(row[7]),
        metadata=row[8] if isinstance(row[8], dict) else {},
    )


def request_approval(
    conn,
    business_slug: str,
    action_kind: str,
    payload,
    *,
    actor: str = "ceo",
    ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
    metadata: dict | None = None,
) -> OperatorApproval:
    """Create (or return, idempotent on the payload digest) a PENDING approval record. The CEO calls
    this when it needs a shape change; the operator decides it out of band (`/approve`, dashboard
    button — the archetypes §1.5 affordances). Re-requesting the SAME payload returns the existing
    record rather than minting a duplicate. Returns the record."""
    digest = payload_digest(payload)
    ttl = max(1, int(ttl_seconds))
    meta = json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True)
    with conn.transaction():
        # ON CONFLICT touches only metadata (idempotent re-request); it NEVER resets the status or a
        # prior decision, so re-requesting an already-approved/denied change does not reopen it.
        row = conn.execute(
            "insert into operator_approvals "
            "(business_slug, action_kind, payload_digest, status, actor, expires_at, metadata_json) "
            "values (%s, %s, %s, 'pending', %s, now() + make_interval(secs => %s), %s::jsonb) "
            "on conflict (business_slug, action_kind, payload_digest) do update set "
            "  metadata_json = excluded.metadata_json "
            f"returning {_APPROVAL_COLUMNS}",
            (business_slug, action_kind, digest, actor, ttl, meta),
        ).fetchone()
    return _approval_from_row(row)


def decide_approval(
    conn,
    business_slug: str,
    action_kind: str,
    payload,
    *,
    approve: bool,
    actor: str = "operator",
    receipt_path: str | None = None,
) -> OperatorApproval:
    """Operator decision on a pending approval (the `/approve` / `/deny` affordance). Flips a
    PENDING record to 'approved' or 'denied'. Refuses if there is no pending record for this payload.
    Returns the decided record."""
    digest = payload_digest(payload)
    new_status = "approved" if approve else "denied"
    with conn.transaction():
        row = conn.execute(
            "update operator_approvals set status = %s, decided_at = now(), actor = %s, "
            "  receipt_path = coalesce(%s, receipt_path) "
            "where business_slug = %s and action_kind = %s and payload_digest = %s "
            "  and status = 'pending' "
            f"returning {_APPROVAL_COLUMNS}",
            (new_status, actor, receipt_path, business_slug, action_kind, digest),
        ).fetchone()
    if row is None:
        raise MoneyShapeError(
            f"no pending approval to {new_status} for action_kind={action_kind!r} on {business_slug}"
        )
    return _approval_from_row(row)


def consume_approval(
    conn,
    business_slug: str,
    action_kind: str,
    payload,
) -> OperatorApproval:
    """Single-consume an APPROVED, non-expired approval for exactly this payload, flipping it to
    'consumed' so it can never authorize a second change. Fails closed (`ApprovalRequired`) when no
    approved record exists, when it has expired, or when it was already consumed — the caller (the
    money-shape change) must not proceed. Caller already holds a transaction (or opens one)."""
    digest = payload_digest(payload)
    # Atomic single-consume: only an 'approved', unexpired row flips to 'consumed'.
    row = conn.execute(
        "update operator_approvals set status = 'consumed', consumed_at = now() "
        "where business_slug = %s and action_kind = %s and payload_digest = %s "
        "  and status = 'approved' "
        "  and (expires_at is null or expires_at > now()) "
        f"returning {_APPROVAL_COLUMNS}",
        (business_slug, action_kind, digest),
    ).fetchone()
    if row is None:
        # Mark an expired approval so it stops reading as actionable (best-effort; the refusal below
        # is the real gate).
        conn.execute(
            "update operator_approvals set status = 'expired' "
            "where business_slug = %s and action_kind = %s and payload_digest = %s "
            "  and status = 'approved' and expires_at is not null and expires_at <= now()",
            (business_slug, action_kind, digest),
        )
        raise ApprovalRequired(
            f"approval_required:{action_kind}: no approved, unconsumed, unexpired operator approval "
            f"for this exact change on {business_slug}. Request one and have the operator approve it "
            "before changing the money shape; the change is refused until the approval exists."
        )
    return _approval_from_row(row)


def get_approval(
    conn, business_slug: str, action_kind: str, payload
) -> OperatorApproval | None:
    """Read the current approval record for a payload, or None. Pure read."""
    digest = payload_digest(payload)
    row = conn.execute(
        f"select {_APPROVAL_COLUMNS} from operator_approvals "
        "where business_slug = %s and action_kind = %s and payload_digest = %s",
        (business_slug, action_kind, digest),
    ).fetchone()
    return None if row is None else _approval_from_row(row)
