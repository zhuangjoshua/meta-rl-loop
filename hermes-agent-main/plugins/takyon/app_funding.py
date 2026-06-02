"""Product app funding rail — per-subuser plan-funded credits plus business subsidy fallback.

This leaf is intentionally narrower than ``billing.py``: it does NOT hold the top-level Takyon
user's money, and it does NOT replace ``app_usage.py``. Instead it answers one runtime question
for one business app request:

  * how much of this request is paid by the sub-user's own plan-funded credits for the current
    period, and
  * how much (if any) is paid by the business subsidy pool?

The outer business kill-switch remains ``app_usage.reserve_usage`` → ``settle_usage``. This leaf is
the payer split that sits inside that safety envelope.

House style matches the other ledgers: pure psycopg leaf, its own ``conn.transaction()`` per
mutation, idempotent reserve → settle | release, and row locks on the authoritative rows before
checking balances. The business subsidy account carries a cached remaining balance; per-subuser
plan-funded credits are period-scoped allowances derived from plan policy and enforced by summing
this leaf's append-only entries for the current period.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


class AppFundingError(Exception):
    """Base for product app funding errors."""


class AppFundingUserNotFound(AppFundingError):
    """The referenced sub-user does not exist in this business."""


class UnknownFundingReservation(AppFundingError):
    """settle/release referenced a reservation_key that does not exist."""


class InsufficientAppFunding(AppFundingError):
    """The request cannot be covered by period user credits plus subsidy."""

    def __init__(
        self,
        *,
        requested_microusd: int,
        user_credit_remaining_microusd: int,
        user_subsidy_remaining_microusd: int,
        business_subsidy_remaining_microusd: int,
    ) -> None:
        self.requested_microusd = requested_microusd
        self.user_credit_remaining_microusd = user_credit_remaining_microusd
        self.user_subsidy_remaining_microusd = user_subsidy_remaining_microusd
        self.business_subsidy_remaining_microusd = business_subsidy_remaining_microusd
        super().__init__(
            "insufficient_app_funding: "
            f"need {requested_microusd}, "
            f"user_credit {user_credit_remaining_microusd}, "
            f"user_subsidy {user_subsidy_remaining_microusd}, "
            f"business_subsidy {business_subsidy_remaining_microusd}"
        )


@dataclass(frozen=True)
class BusinessSubsidyBalances:
    business_slug: str
    balance_microusd: int


@dataclass(frozen=True)
class FundingReservation:
    key: str
    period_start: object
    user_credit_microusd: int
    subsidy_microusd: int

    @property
    def total_microusd(self) -> int:
        return self.user_credit_microusd + self.subsidy_microusd


@dataclass(frozen=True)
class FundingOutcome:
    key: str
    period_start: object
    reserved_user_credit_microusd: int
    reserved_subsidy_microusd: int
    settled_user_credit_microusd: int
    settled_subsidy_microusd: int
    released_user_credit_microusd: int
    released_subsidy_microusd: int


@dataclass(frozen=True)
class FundingPeriodSummary:
    business_slug: str
    app_user_id: str
    period_start: object
    user_credit_settled_microusd: int
    user_credit_reserved_microusd: int
    subsidy_settled_microusd: int
    subsidy_reserved_microusd: int


_BUCKET_USER_CREDIT = "user_credit"
_BUCKET_SUBSIDY = "subsidy"
_KIND_GRANT = "grant"
_KIND_RESERVE = "reserve"
_KIND_SETTLE = "settle"
_KIND_RELEASE = "release"


def _json_dumps(value) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _require_period_start(period_start) -> object:
    if period_start is None:
        raise ValueError("period_start is required")
    return period_start


def _require_positive_amount(name: str, value: int, *, allow_zero: bool = True) -> int:
    amount = int(value)
    if allow_zero:
        if amount < 0:
            raise ValueError(f"{name} must be >= 0")
    elif amount <= 0:
        raise ValueError(f"{name} must be > 0")
    return amount


def _ensure_business_subsidy_account_locked(conn, business_slug: str) -> BusinessSubsidyBalances:
    conn.execute(
        "insert into app_business_subsidy_accounts (business_slug) "
        "values (%s) on conflict (business_slug) do nothing",
        (business_slug,),
    )
    row = conn.execute(
        "select business_slug, balance_microusd "
        "from app_business_subsidy_accounts where business_slug = %s for update",
        (business_slug,),
    ).fetchone()
    return BusinessSubsidyBalances(business_slug=str(row[0]), balance_microusd=int(row[1]))


def _lock_app_user(conn, business_slug: str, app_user_id: str) -> None:
    row = conn.execute(
        "select 1 from app_users where business_slug = %s and id = %s for update",
        (business_slug, app_user_id),
    ).fetchone()
    if row is None:
        raise AppFundingUserNotFound(app_user_id)


def _existing_reservation(conn, business_slug: str, reservation_key: str) -> FundingReservation | None:
    rows = conn.execute(
        "select bucket, amount_microusd, period_start "
        "from app_funding_entries "
        "where business_slug = %s and reservation_key = %s and kind = %s",
        (business_slug, reservation_key, _KIND_RESERVE),
    ).fetchall()
    if not rows:
        return None
    user_credit = 0
    subsidy = 0
    period_start = rows[0][2]
    for bucket, amount, _period_start in rows:
        if bucket == _BUCKET_USER_CREDIT:
            user_credit += int(amount)
        elif bucket == _BUCKET_SUBSIDY:
            subsidy += int(amount)
    return FundingReservation(
        key=reservation_key,
        period_start=period_start,
        user_credit_microusd=user_credit,
        subsidy_microusd=subsidy,
    )


def _open_reserved_microusd(conn, business_slug: str, app_user_id: str, bucket: str, period_start) -> int:
    row = conn.execute(
        """
        select coalesce(sum(r.amount_microusd), 0)
        from app_funding_entries r
        left join app_funding_entries f
          on f.reservation_key = r.reservation_key
         and f.bucket = r.bucket
         and f.kind in (%s, %s)
        where r.business_slug = %s
          and r.app_user_id = %s
          and r.bucket = %s
          and r.kind = %s
          and r.period_start = %s
          and f.id is null
        """,
        (
            _KIND_SETTLE,
            _KIND_RELEASE,
            business_slug,
            app_user_id,
            bucket,
            _KIND_RESERVE,
            period_start,
        ),
    ).fetchone()
    return int(row[0] or 0)


def _settled_microusd(conn, business_slug: str, app_user_id: str, bucket: str, period_start) -> int:
    row = conn.execute(
        "select coalesce(sum(amount_microusd), 0) "
        "from app_funding_entries "
        "where business_slug = %s and app_user_id = %s and bucket = %s and kind = %s "
        "and period_start = %s",
        (business_slug, app_user_id, bucket, _KIND_SETTLE, period_start),
    ).fetchone()
    return int(row[0] or 0)


def get_business_subsidy_balances(conn, business_slug: str) -> BusinessSubsidyBalances:
    row = conn.execute(
        "select business_slug, balance_microusd "
        "from app_business_subsidy_accounts where business_slug = %s",
        (business_slug,),
    ).fetchone()
    if row is None:
        return BusinessSubsidyBalances(business_slug=business_slug, balance_microusd=0)
    return BusinessSubsidyBalances(business_slug=str(row[0]), balance_microusd=int(row[1]))


def grant_business_subsidy(
    conn,
    business_slug: str,
    amount_microusd: int,
    idempotency_key: str,
    *,
    metadata: dict | None = None,
) -> BusinessSubsidyBalances:
    amount = _require_positive_amount("amount_microusd", amount_microusd, allow_zero=False)
    key = str(idempotency_key or "").strip()
    if not key:
        raise ValueError("idempotency_key is required")
    with conn.transaction():
        balances = _ensure_business_subsidy_account_locked(conn, business_slug)
        prior = conn.execute(
            "select balance_after_microusd from app_funding_entries where idempotency_key = %s",
            (key,),
        ).fetchone()
        if prior is not None:
            return BusinessSubsidyBalances(business_slug=business_slug, balance_microusd=int(prior[0]))
        new_balance = balances.balance_microusd + amount
        conn.execute(
            "update app_business_subsidy_accounts set balance_microusd = %s, updated_at = now() "
            "where business_slug = %s",
            (new_balance, business_slug),
        )
        conn.execute(
            "insert into app_funding_entries "
            "(business_slug, app_user_id, plan_key, bucket, kind, amount_microusd, "
            " balance_after_microusd, period_start, reservation_key, idempotency_key, metadata) "
            "values (%s, null, null, %s, %s, %s, %s, now(), null, %s, %s::jsonb)",
            (
                business_slug,
                _BUCKET_SUBSIDY,
                _KIND_GRANT,
                amount,
                new_balance,
                key,
                _json_dumps(metadata),
            ),
        )
        return BusinessSubsidyBalances(business_slug=business_slug, balance_microusd=new_balance)


def reserve_funding(
    conn,
    business_slug: str,
    *,
    app_user_id: str,
    reservation_key: str,
    estimated_cost_microusd: int,
    user_credit_limit_microusd: int,
    subsidy_cap_microusd: int,
    period_start,
    plan_key: str | None = None,
    metadata: dict | None = None,
) -> FundingReservation:
    requested = _require_positive_amount("estimated_cost_microusd", estimated_cost_microusd)
    user_limit = _require_positive_amount("user_credit_limit_microusd", user_credit_limit_microusd)
    subsidy_cap = _require_positive_amount("subsidy_cap_microusd", subsidy_cap_microusd)
    period = _require_period_start(period_start)
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    with conn.transaction():
        subsidy_balances = _ensure_business_subsidy_account_locked(conn, business_slug)
        _lock_app_user(conn, business_slug, app_user_id)
        existing = _existing_reservation(conn, business_slug, key)
        if existing is not None:
            return existing

        user_credit_settled = _settled_microusd(
            conn, business_slug, app_user_id, _BUCKET_USER_CREDIT, period
        )
        user_credit_reserved = _open_reserved_microusd(
            conn, business_slug, app_user_id, _BUCKET_USER_CREDIT, period
        )
        user_credit_remaining = max(0, user_limit - user_credit_settled - user_credit_reserved)

        subsidy_settled = _settled_microusd(
            conn, business_slug, app_user_id, _BUCKET_SUBSIDY, period
        )
        subsidy_reserved = _open_reserved_microusd(
            conn, business_slug, app_user_id, _BUCKET_SUBSIDY, period
        )
        user_subsidy_remaining = max(0, subsidy_cap - subsidy_settled - subsidy_reserved)

        user_credit_alloc = min(requested, user_credit_remaining)
        subsidy_needed = requested - user_credit_alloc
        subsidy_alloc = min(
            subsidy_needed,
            user_subsidy_remaining,
            subsidy_balances.balance_microusd,
        )

        if user_credit_alloc + subsidy_alloc < requested:
            raise InsufficientAppFunding(
                requested_microusd=requested,
                user_credit_remaining_microusd=user_credit_remaining,
                user_subsidy_remaining_microusd=user_subsidy_remaining,
                business_subsidy_remaining_microusd=subsidy_balances.balance_microusd,
            )

        if user_credit_alloc > 0 or requested == 0:
            conn.execute(
                "insert into app_funding_entries "
                "(business_slug, app_user_id, plan_key, bucket, kind, amount_microusd, "
                " balance_after_microusd, period_start, reservation_key, idempotency_key, metadata) "
                "values (%s, %s, %s, %s, %s, %s, null, %s, %s, %s, %s::jsonb)",
                (
                    business_slug,
                    app_user_id,
                    plan_key,
                    _BUCKET_USER_CREDIT,
                    _KIND_RESERVE,
                    user_credit_alloc,
                    period,
                    key,
                    f"{key}:reserve:{_BUCKET_USER_CREDIT}",
                    _json_dumps(metadata),
                ),
            )
        if subsidy_alloc > 0:
            new_balance = subsidy_balances.balance_microusd - subsidy_alloc
            conn.execute(
                "update app_business_subsidy_accounts set balance_microusd = %s, updated_at = now() "
                "where business_slug = %s",
                (new_balance, business_slug),
            )
            conn.execute(
                "insert into app_funding_entries "
                "(business_slug, app_user_id, plan_key, bucket, kind, amount_microusd, "
                " balance_after_microusd, period_start, reservation_key, idempotency_key, metadata) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                (
                    business_slug,
                    app_user_id,
                    plan_key,
                    _BUCKET_SUBSIDY,
                    _KIND_RESERVE,
                    subsidy_alloc,
                    new_balance,
                    period,
                    key,
                    f"{key}:reserve:{_BUCKET_SUBSIDY}",
                    _json_dumps(metadata),
                ),
            )
        return FundingReservation(
            key=key,
            period_start=period,
            user_credit_microusd=user_credit_alloc,
            subsidy_microusd=subsidy_alloc,
        )


def _reservation_outcome_rows(conn, reservation_key: str):
    reserve_rows = conn.execute(
        "select business_slug, app_user_id, bucket, amount_microusd, period_start "
        "from app_funding_entries where reservation_key = %s and kind = %s "
        "order by id asc",
        (reservation_key, _KIND_RESERVE),
    ).fetchall()
    if not reserve_rows:
        raise UnknownFundingReservation(reservation_key)
    final_rows = conn.execute(
        "select bucket, kind, amount_microusd from app_funding_entries "
        "where reservation_key = %s and kind in (%s, %s)",
        (reservation_key, _KIND_SETTLE, _KIND_RELEASE),
    ).fetchall()
    return reserve_rows, final_rows


def _outcome_from_rows(reserve_rows, final_rows, reservation_key: str) -> FundingOutcome:
    reserved_user_credit = 0
    reserved_subsidy = 0
    settled_user_credit = 0
    settled_subsidy = 0
    released_user_credit = 0
    released_subsidy = 0
    period_start = reserve_rows[0][4]
    for _business_slug, _app_user_id, bucket, amount, _period_start in reserve_rows:
        if bucket == _BUCKET_USER_CREDIT:
            reserved_user_credit += int(amount)
        elif bucket == _BUCKET_SUBSIDY:
            reserved_subsidy += int(amount)
    for bucket, kind, amount in final_rows:
        if bucket == _BUCKET_USER_CREDIT and kind == _KIND_SETTLE:
            settled_user_credit += int(amount)
        elif bucket == _BUCKET_SUBSIDY and kind == _KIND_SETTLE:
            settled_subsidy += int(amount)
        elif bucket == _BUCKET_USER_CREDIT and kind == _KIND_RELEASE:
            released_user_credit += int(amount)
        elif bucket == _BUCKET_SUBSIDY and kind == _KIND_RELEASE:
            released_subsidy += int(amount)
    return FundingOutcome(
        key=reservation_key,
        period_start=period_start,
        reserved_user_credit_microusd=reserved_user_credit,
        reserved_subsidy_microusd=reserved_subsidy,
        settled_user_credit_microusd=settled_user_credit,
        settled_subsidy_microusd=settled_subsidy,
        released_user_credit_microusd=released_user_credit,
        released_subsidy_microusd=released_subsidy,
    )


def settle_funding(
    conn,
    reservation_key: str,
    *,
    actual_cost_microusd: int,
    metadata: dict | None = None,
) -> FundingOutcome:
    actual = _require_positive_amount("actual_cost_microusd", actual_cost_microusd)
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    with conn.transaction():
        reserve_rows, final_rows = _reservation_outcome_rows(conn, key)
        if final_rows:
            return _outcome_from_rows(reserve_rows, final_rows, key)

        business_slug = str(reserve_rows[0][0])
        app_user_id = None if reserve_rows[0][1] is None else str(reserve_rows[0][1])
        period_start = reserve_rows[0][4]
        _ensure_business_subsidy_account_locked(conn, business_slug)
        if app_user_id is not None:
            _lock_app_user(conn, business_slug, app_user_id)

        reserved_user_credit = sum(
            int(amount) for _b, _u, bucket, amount, _p in reserve_rows if bucket == _BUCKET_USER_CREDIT
        )
        reserved_subsidy = sum(
            int(amount) for _b, _u, bucket, amount, _p in reserve_rows if bucket == _BUCKET_SUBSIDY
        )
        reserved_total = reserved_user_credit + reserved_subsidy
        if actual > reserved_total:
            raise ValueError(f"actual {actual} exceeds reserved {reserved_total}")

        settled_user_credit = min(actual, reserved_user_credit)
        settled_subsidy = actual - settled_user_credit
        released_user_credit = reserved_user_credit - settled_user_credit
        released_subsidy = reserved_subsidy - settled_subsidy

        subsidy_balances = _ensure_business_subsidy_account_locked(conn, business_slug)
        new_subsidy_balance = subsidy_balances.balance_microusd + released_subsidy
        if released_subsidy > 0:
            conn.execute(
                "update app_business_subsidy_accounts set balance_microusd = %s, updated_at = now() "
                "where business_slug = %s",
                (new_subsidy_balance, business_slug),
            )

        if settled_user_credit > 0:
            conn.execute(
                "insert into app_funding_entries "
                "(business_slug, app_user_id, plan_key, bucket, kind, amount_microusd, "
                " balance_after_microusd, period_start, reservation_key, idempotency_key, metadata) "
                "values (%s, %s, null, %s, %s, %s, null, %s, %s, %s, %s::jsonb)",
                (
                    business_slug,
                    app_user_id,
                    _BUCKET_USER_CREDIT,
                    _KIND_SETTLE,
                    settled_user_credit,
                    period_start,
                    key,
                    f"{key}:settle:{_BUCKET_USER_CREDIT}",
                    _json_dumps(metadata),
                ),
            )
        if settled_subsidy > 0:
            conn.execute(
                "insert into app_funding_entries "
                "(business_slug, app_user_id, plan_key, bucket, kind, amount_microusd, "
                " balance_after_microusd, period_start, reservation_key, idempotency_key, metadata) "
                "values (%s, %s, null, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                (
                    business_slug,
                    app_user_id,
                    _BUCKET_SUBSIDY,
                    _KIND_SETTLE,
                    settled_subsidy,
                    subsidy_balances.balance_microusd,
                    period_start,
                    key,
                    f"{key}:settle:{_BUCKET_SUBSIDY}",
                    _json_dumps(metadata),
                ),
            )
        if released_user_credit > 0:
            conn.execute(
                "insert into app_funding_entries "
                "(business_slug, app_user_id, plan_key, bucket, kind, amount_microusd, "
                " balance_after_microusd, period_start, reservation_key, idempotency_key, metadata) "
                "values (%s, %s, null, %s, %s, %s, null, %s, %s, %s, %s::jsonb)",
                (
                    business_slug,
                    app_user_id,
                    _BUCKET_USER_CREDIT,
                    _KIND_RELEASE,
                    released_user_credit,
                    period_start,
                    key,
                    f"{key}:release:{_BUCKET_USER_CREDIT}",
                    _json_dumps(metadata),
                ),
            )
        if released_subsidy > 0:
            conn.execute(
                "insert into app_funding_entries "
                "(business_slug, app_user_id, plan_key, bucket, kind, amount_microusd, "
                " balance_after_microusd, period_start, reservation_key, idempotency_key, metadata) "
                "values (%s, %s, null, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                (
                    business_slug,
                    app_user_id,
                    _BUCKET_SUBSIDY,
                    _KIND_RELEASE,
                    released_subsidy,
                    new_subsidy_balance,
                    period_start,
                    key,
                    f"{key}:release:{_BUCKET_SUBSIDY}",
                    _json_dumps(metadata),
                ),
            )
        return FundingOutcome(
            key=key,
            period_start=period_start,
            reserved_user_credit_microusd=reserved_user_credit,
            reserved_subsidy_microusd=reserved_subsidy,
            settled_user_credit_microusd=settled_user_credit,
            settled_subsidy_microusd=settled_subsidy,
            released_user_credit_microusd=released_user_credit,
            released_subsidy_microusd=released_subsidy,
        )


def release_funding(
    conn,
    reservation_key: str,
    *,
    metadata: dict | None = None,
) -> FundingOutcome:
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    with conn.transaction():
        reserve_rows, final_rows = _reservation_outcome_rows(conn, key)
        if final_rows:
            return _outcome_from_rows(reserve_rows, final_rows, key)

        business_slug = str(reserve_rows[0][0])
        app_user_id = None if reserve_rows[0][1] is None else str(reserve_rows[0][1])
        period_start = reserve_rows[0][4]
        reserved_user_credit = sum(
            int(amount) for _b, _u, bucket, amount, _p in reserve_rows if bucket == _BUCKET_USER_CREDIT
        )
        reserved_subsidy = sum(
            int(amount) for _b, _u, bucket, amount, _p in reserve_rows if bucket == _BUCKET_SUBSIDY
        )
        subsidy_balances = _ensure_business_subsidy_account_locked(conn, business_slug)
        if app_user_id is not None:
            _lock_app_user(conn, business_slug, app_user_id)
        new_subsidy_balance = subsidy_balances.balance_microusd + reserved_subsidy
        if reserved_subsidy > 0:
            conn.execute(
                "update app_business_subsidy_accounts set balance_microusd = %s, updated_at = now() "
                "where business_slug = %s",
                (new_subsidy_balance, business_slug),
            )
        if reserved_user_credit > 0:
            conn.execute(
                "insert into app_funding_entries "
                "(business_slug, app_user_id, plan_key, bucket, kind, amount_microusd, "
                " balance_after_microusd, period_start, reservation_key, idempotency_key, metadata) "
                "values (%s, %s, null, %s, %s, %s, null, %s, %s, %s, %s::jsonb)",
                (
                    business_slug,
                    app_user_id,
                    _BUCKET_USER_CREDIT,
                    _KIND_RELEASE,
                    reserved_user_credit,
                    period_start,
                    key,
                    f"{key}:release:{_BUCKET_USER_CREDIT}",
                    _json_dumps(metadata),
                ),
            )
        if reserved_subsidy > 0:
            conn.execute(
                "insert into app_funding_entries "
                "(business_slug, app_user_id, plan_key, bucket, kind, amount_microusd, "
                " balance_after_microusd, period_start, reservation_key, idempotency_key, metadata) "
                "values (%s, %s, null, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                (
                    business_slug,
                    app_user_id,
                    _BUCKET_SUBSIDY,
                    _KIND_RELEASE,
                    reserved_subsidy,
                    new_subsidy_balance,
                    period_start,
                    key,
                    f"{key}:release:{_BUCKET_SUBSIDY}",
                    _json_dumps(metadata),
                ),
            )
        return FundingOutcome(
            key=key,
            period_start=period_start,
            reserved_user_credit_microusd=reserved_user_credit,
            reserved_subsidy_microusd=reserved_subsidy,
            settled_user_credit_microusd=0,
            settled_subsidy_microusd=0,
            released_user_credit_microusd=reserved_user_credit,
            released_subsidy_microusd=reserved_subsidy,
        )


def get_user_period_funding_summary(
    conn,
    business_slug: str,
    app_user_id: str,
    *,
    period_start,
) -> FundingPeriodSummary:
    period = _require_period_start(period_start)
    return FundingPeriodSummary(
        business_slug=business_slug,
        app_user_id=app_user_id,
        period_start=period,
        user_credit_settled_microusd=_settled_microusd(
            conn, business_slug, app_user_id, _BUCKET_USER_CREDIT, period
        ),
        user_credit_reserved_microusd=_open_reserved_microusd(
            conn, business_slug, app_user_id, _BUCKET_USER_CREDIT, period
        ),
        subsidy_settled_microusd=_settled_microusd(
            conn, business_slug, app_user_id, _BUCKET_SUBSIDY, period
        ),
        subsidy_reserved_microusd=_open_reserved_microusd(
            conn, business_slug, app_user_id, _BUCKET_SUBSIDY, period
        ),
    )
