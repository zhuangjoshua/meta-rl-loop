"""Billing ledger — flow A (user → platform), the compute-spend side of the money
model in mediationplan.md.

Two buckets per user, spent allowance-first then topup:
  * allowance — included usage, an opaque metering unit. Stored in cents for
    accounting only; it is NEVER money and must never be surfaced as dollars.
  * topup     — exact money the user paid in.

Costly work is wrapped in reserve → settle/refund:
  reserve(estimate) holds funds (allowance first, then topup); on success
  settle(actual) records the real spend and releases (estimate − actual); on
  failure refund() releases the whole reservation. Neither bucket covering the
  estimate raises InsufficientBalance (the caller maps that to a 402 / blocked job).

Concurrency + idempotency rest on ONE invariant: every mutating op takes
`SELECT … FOR UPDATE` on the single billing_accounts row BEFORE it checks for a
prior effect or writes anything. That row lock serializes all of a user's ledger
writes, so parallel reserves can never oversell and a replayed idempotency key can
never double-charge. Cached balances are always re-derivable from the append-only
billing_entries; `reconcile_billing` proves it.

Backend-agnostic: takes a psycopg connection, opens its own `conn.transaction()`
per op (works whether the connection is autocommit or not), imports no psycopg.
"""

from __future__ import annotations

from dataclasses import dataclass


class BillingError(Exception):
    """Base for billing-ledger errors."""


class NoBillingAccount(BillingError):
    """No billing_accounts row for this user (provisioning should have opened one)."""


class UnknownReservation(BillingError):
    """settle/refund referenced a reservation_key that was never reserved."""


class InsufficientBalance(BillingError):
    """Neither allowance nor topup can cover the estimate. Carries the exact figures
    so the caller can build a precise 402 without leaking anything else."""

    def __init__(
        self,
        *,
        estimate_cents: int,
        allowance_available_cents: int,
        topup_available_cents: int,
    ) -> None:
        self.estimate_cents = estimate_cents
        self.allowance_available_cents = allowance_available_cents
        self.topup_available_cents = topup_available_cents
        super().__init__(
            f"insufficient_balance: need {estimate_cents}, "
            f"allowance {allowance_available_cents} + topup {topup_available_cents}"
        )


@dataclass(frozen=True)
class Reservation:
    """The outcome of a reserve: how the estimate was split across buckets. `key` is
    the reservation_key threaded into settle/refund."""

    key: str
    allowance_cents: int
    topup_cents: int

    @property
    def total_cents(self) -> int:
        return self.allowance_cents + self.topup_cents


@dataclass(frozen=True)
class BillingBalances:
    user_id: str
    allowance_included_cents: int
    allowance_used_cents: int
    topup_balance_cents: int
    allowance_remaining_cents: int
    reserved_cents: int


def open_billing_account(conn, user_id: str, *, allowance_included_cents: int = 0) -> None:
    """Open the user's single billing account. Idempotent and transaction-free so it
    composes inside the provisioning transaction. Default allowance is 0 — the free /
    plan grant amount is a policy decision applied later via `grant_allowance`, not a
    number invented here."""
    conn.execute(
        "insert into billing_accounts (user_id, allowance_included_cents) "
        "values (%s, %s) on conflict (user_id) do nothing",
        (user_id, allowance_included_cents),
    )


def grant_allowance(
    conn,
    user_id: str,
    included_cents: int,
    idempotency_key: str,
    *,
    period_start=None,
    resets_at=None,
) -> int:
    """Set the period's included allowance and reset used to 0 (a fresh metering
    period). Assumes no allowance reservations are outstanding across the reset.
    Idempotent on `idempotency_key`. Returns the included amount in effect."""
    if included_cents < 0:
        raise ValueError("included_cents must be >= 0")
    with conn.transaction():
        acct = conn.execute(
            "select allowance_included_cents from billing_accounts "
            "where user_id = %s for update",
            (user_id,),
        ).fetchone()
        if acct is None:
            raise NoBillingAccount(user_id)
        if _entry_exists(conn, idempotency_key):
            return int(acct[0])
        conn.execute(
            "update billing_accounts set allowance_included_cents = %s, "
            "allowance_used_cents = 0, allowance_period_start = coalesce(%s, now()), "
            "allowance_resets_at = %s, updated_at = now() where user_id = %s",
            (included_cents, period_start, resets_at, user_id),
        )
        conn.execute(
            "insert into billing_entries (user_id, bucket, kind, amount_cents, "
            "balance_after_cents, idempotency_key) "
            "values (%s, 'allowance', 'grant', %s, 0, %s)",
            (user_id, included_cents, idempotency_key),
        )
    return included_cents


def topup(conn, user_id: str, amount_cents: int, idempotency_key: str) -> int:
    """Credit exact money into the topup bucket. Idempotent on `idempotency_key`
    (e.g. a Stripe event id → replayed webhook credits once). Returns the new topup
    balance."""
    if amount_cents <= 0:
        raise ValueError("amount_cents must be > 0")
    with conn.transaction():
        acct = conn.execute(
            "select topup_balance_cents from billing_accounts "
            "where user_id = %s for update",
            (user_id,),
        ).fetchone()
        if acct is None:
            raise NoBillingAccount(user_id)
        prior = conn.execute(
            "select balance_after_cents from billing_entries where idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()
        if prior is not None:
            return int(prior[0])
        new_balance = int(acct[0]) + amount_cents
        conn.execute(
            "update billing_accounts set topup_balance_cents = %s, updated_at = now() "
            "where user_id = %s",
            (new_balance, user_id),
        )
        conn.execute(
            "insert into billing_entries (user_id, bucket, kind, amount_cents, "
            "balance_after_cents, idempotency_key) "
            "values (%s, 'topup', 'topup', %s, %s, %s)",
            (user_id, amount_cents, new_balance, idempotency_key),
        )
    return new_balance


def reserve(
    conn,
    user_id: str,
    estimate_cents: int,
    idempotency_key: str,
    *,
    business_slug: str | None = None,
    job_id: str | None = None,
) -> Reservation:
    """Hold `estimate_cents`, allowance first then topup, against the user's account.
    `idempotency_key` becomes the reservation_key; a replay returns the SAME split
    without moving balances. Raises InsufficientBalance if the two buckets together
    cannot cover the estimate (no entries written)."""
    if estimate_cents < 0:
        raise ValueError("estimate_cents must be >= 0")
    rk = idempotency_key
    with conn.transaction():
        acct = conn.execute(
            "select allowance_included_cents, allowance_used_cents, topup_balance_cents "
            "from billing_accounts where user_id = %s for update",
            (user_id,),
        ).fetchone()
        if acct is None:
            raise NoBillingAccount(user_id)
        included, used, topup_bal = int(acct[0]), int(acct[1]), int(acct[2])
        existing = conn.execute(
            "select bucket, amount_cents from billing_entries "
            "where reservation_key = %s and kind = 'reserve'",
            (rk,),
        ).fetchall()
        if existing:
            a = sum(int(r[1]) for r in existing if r[0] == "allowance")
            t = sum(int(r[1]) for r in existing if r[0] == "topup")
            return Reservation(key=rk, allowance_cents=a, topup_cents=t)
        avail_allow = max(0, included - used)
        a_alloc = min(estimate_cents, avail_allow)
        t_need = estimate_cents - a_alloc
        if t_need > topup_bal:
            raise InsufficientBalance(
                estimate_cents=estimate_cents,
                allowance_available_cents=avail_allow,
                topup_available_cents=topup_bal,
            )
        new_used = used + a_alloc
        new_topup = topup_bal - t_need
        conn.execute(
            "update billing_accounts set allowance_used_cents = %s, "
            "topup_balance_cents = %s, updated_at = now() where user_id = %s",
            (new_used, new_topup, user_id),
        )
        # Allowance entry: written when it carries cost, or as a zero anchor for a
        # zero-estimate reservation so the reservation_key still exists and replays.
        if a_alloc > 0 or t_need == 0:
            _insert_entry(
                conn, user_id, business_slug, "allowance", "reserve",
                a_alloc, new_used, rk, job_id, f"{rk}:reserve:allowance",
            )
        if t_need > 0:
            _insert_entry(
                conn, user_id, business_slug, "topup", "reserve",
                t_need, new_topup, rk, job_id, f"{rk}:reserve:topup",
            )
        return Reservation(key=rk, allowance_cents=a_alloc, topup_cents=t_need)


def settle(conn, reservation_key: str, actual_cents: int) -> None:
    """Finalize a reservation at `actual_cents` (≤ reserved): record the real spend
    and release the unused remainder. Idempotent — a second settle/refund on the same
    reservation is a no-op (first finalizer wins)."""
    if actual_cents < 0:
        raise ValueError("actual_cents must be >= 0")
    rk = reservation_key
    with conn.transaction():
        resv = conn.execute(
            "select user_id, bucket, amount_cents from billing_entries "
            "where reservation_key = %s and kind = 'reserve'",
            (rk,),
        ).fetchall()
        if not resv:
            raise UnknownReservation(rk)
        user_id = str(resv[0][0])
        a_resv = sum(int(r[2]) for r in resv if r[1] == "allowance")
        t_resv = sum(int(r[2]) for r in resv if r[1] == "topup")
        if actual_cents > a_resv + t_resv:
            raise ValueError(f"actual {actual_cents} exceeds reserved {a_resv + t_resv}")
        acct = conn.execute(
            "select allowance_used_cents, topup_balance_cents from billing_accounts "
            "where user_id = %s for update",
            (user_id,),
        ).fetchone()
        if _finalized(conn, rk):
            return
        used, topup_bal = int(acct[0]), int(acct[1])
        # Spend allowance first; the remainder of each bucket's reservation is released.
        s_alloc = min(actual_cents, a_resv)
        s_top = actual_cents - s_alloc
        r_alloc = a_resv - s_alloc
        r_top = t_resv - s_top
        new_used = used - r_alloc
        new_topup = topup_bal + r_top
        conn.execute(
            "update billing_accounts set allowance_used_cents = %s, "
            "topup_balance_cents = %s, updated_at = now() where user_id = %s",
            (new_used, new_topup, user_id),
        )
        # settle does not move balances (it reclassifies held → spent), so its
        # balance_after is the pre-release value; refund carries the released value.
        if s_alloc > 0:
            _insert_entry(conn, user_id, None, "allowance", "settle",
                          s_alloc, used, rk, None, f"{rk}:settle:allowance")
        if s_top > 0:
            _insert_entry(conn, user_id, None, "topup", "settle",
                          s_top, topup_bal, rk, None, f"{rk}:settle:topup")
        if r_alloc > 0:
            _insert_entry(conn, user_id, None, "allowance", "refund",
                          r_alloc, new_used, rk, None, f"{rk}:refund:allowance")
        if r_top > 0:
            _insert_entry(conn, user_id, None, "topup", "refund",
                          r_top, new_topup, rk, None, f"{rk}:refund:topup")


def refund(conn, reservation_key: str) -> None:
    """Release a whole reservation (the failure path). Idempotent — a no-op if the
    reservation was already settled or refunded."""
    rk = reservation_key
    with conn.transaction():
        resv = conn.execute(
            "select user_id, bucket, amount_cents from billing_entries "
            "where reservation_key = %s and kind = 'reserve'",
            (rk,),
        ).fetchall()
        if not resv:
            raise UnknownReservation(rk)
        user_id = str(resv[0][0])
        a_resv = sum(int(r[2]) for r in resv if r[1] == "allowance")
        t_resv = sum(int(r[2]) for r in resv if r[1] == "topup")
        acct = conn.execute(
            "select allowance_used_cents, topup_balance_cents from billing_accounts "
            "where user_id = %s for update",
            (user_id,),
        ).fetchone()
        if _finalized(conn, rk):
            return
        used, topup_bal = int(acct[0]), int(acct[1])
        new_used = used - a_resv
        new_topup = topup_bal + t_resv
        conn.execute(
            "update billing_accounts set allowance_used_cents = %s, "
            "topup_balance_cents = %s, updated_at = now() where user_id = %s",
            (new_used, new_topup, user_id),
        )
        if a_resv > 0:
            _insert_entry(conn, user_id, None, "allowance", "refund",
                          a_resv, new_used, rk, None, f"{rk}:refund:allowance")
        if t_resv > 0:
            _insert_entry(conn, user_id, None, "topup", "refund",
                          t_resv, new_topup, rk, None, f"{rk}:refund:topup")


def get_billing_balances(conn, user_id: str) -> BillingBalances:
    """Read the cached balances plus derived allowance remaining and outstanding
    reserved (Σreserve − Σsettle − Σrefund). Allowance figures are metering units —
    callers must not render them as money."""
    acct = conn.execute(
        "select allowance_included_cents, allowance_used_cents, topup_balance_cents "
        "from billing_accounts where user_id = %s",
        (user_id,),
    ).fetchone()
    if acct is None:
        raise NoBillingAccount(user_id)
    included, used, topup_bal = int(acct[0]), int(acct[1]), int(acct[2])
    reserved = conn.execute(
        "select coalesce(sum(amount_cents) filter (where kind = 'reserve'), 0) "
        "- coalesce(sum(amount_cents) filter (where kind = 'settle'), 0) "
        "- coalesce(sum(amount_cents) filter (where kind = 'refund'), 0) "
        "from billing_entries where user_id = %s",
        (user_id,),
    ).fetchone()[0]
    return BillingBalances(
        user_id=user_id,
        allowance_included_cents=included,
        allowance_used_cents=used,
        topup_balance_cents=topup_bal,
        allowance_remaining_cents=included - used,
        reserved_cents=int(reserved),
    )


def reconcile_billing(conn, user_id: str) -> dict:
    """Prove the cached balances equal the append-only ledger and no reservation went
    negative. Returns a dict with `ok` and, on drift, the mismatched figures. Allowance
    is reconciled within the current period (since allowance_period_start); topup over
    all time."""
    acct = conn.execute(
        "select allowance_included_cents, allowance_used_cents, topup_balance_cents, "
        "allowance_period_start from billing_accounts where user_id = %s",
        (user_id,),
    ).fetchone()
    if acct is None:
        raise NoBillingAccount(user_id)
    included, used, topup_bal, period_start = (
        int(acct[0]), int(acct[1]), int(acct[2]), acct[3],
    )
    sums = conn.execute(
        "select "
        " coalesce(sum(amount_cents) filter (where bucket='allowance' and kind='reserve' and (%(ps)s::timestamptz is null or created_at >= %(ps)s::timestamptz)), 0),"
        " coalesce(sum(amount_cents) filter (where bucket='allowance' and kind='settle'  and (%(ps)s::timestamptz is null or created_at >= %(ps)s::timestamptz)), 0),"
        " coalesce(sum(amount_cents) filter (where bucket='allowance' and kind='refund'  and (%(ps)s::timestamptz is null or created_at >= %(ps)s::timestamptz)), 0),"
        " coalesce(sum(amount_cents) filter (where bucket='topup' and kind='topup'), 0),"
        " coalesce(sum(amount_cents) filter (where bucket='topup' and kind='debit'), 0),"
        " coalesce(sum(amount_cents) filter (where bucket='topup' and kind='reserve'), 0),"
        " coalesce(sum(amount_cents) filter (where bucket='topup' and kind='settle'), 0),"
        " coalesce(sum(amount_cents) filter (where bucket='topup' and kind='refund'), 0)"
        " from billing_entries where user_id = %(uid)s",
        {"ps": period_start, "uid": user_id},
    ).fetchone()
    a_res, a_set, a_ref, t_top, t_deb, t_res, t_set, t_ref = (int(x) for x in sums)
    calc_used = a_res - a_ref
    calc_topup = t_top - t_deb - t_res + t_ref
    reserved_allowance = a_res - a_set - a_ref
    reserved_topup = t_res - t_set - t_ref
    drift = {}
    if calc_used != used:
        drift["allowance_used"] = {"cached": used, "ledger": calc_used}
    if calc_topup != topup_bal:
        drift["topup_balance"] = {"cached": topup_bal, "ledger": calc_topup}
    if reserved_allowance < 0:
        drift["reserved_allowance_negative"] = reserved_allowance
    if reserved_topup < 0:
        drift["reserved_topup_negative"] = reserved_topup
    if used > included:
        drift["allowance_oversold"] = {"used": used, "included": included}
    return {
        "ok": not drift,
        "drift": drift,
        "reserved_cents": reserved_allowance + reserved_topup,
    }


def _entry_exists(conn, idempotency_key: str) -> bool:
    return (
        conn.execute(
            "select 1 from billing_entries where idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()
        is not None
    )


def _finalized(conn, reservation_key: str) -> bool:
    return (
        conn.execute(
            "select 1 from billing_entries where reservation_key = %s "
            "and kind in ('settle', 'refund') limit 1",
            (reservation_key,),
        ).fetchone()
        is not None
    )


def _insert_entry(
    conn, user_id, business_slug, bucket, kind, amount, balance_after,
    reservation_key, job_id, idempotency_key,
) -> None:
    conn.execute(
        "insert into billing_entries (user_id, business_slug, bucket, kind, "
        "amount_cents, balance_after_cents, reservation_key, job_id, idempotency_key) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (user_id, business_slug, bucket, kind, amount, balance_after,
         reservation_key, job_id, idempotency_key),
    )
