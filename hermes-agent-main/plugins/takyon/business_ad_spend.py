"""Guarded backend policy rows for live business ad-campaign spend."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass


class BusinessAdSpendError(Exception):
    """Base for backend ad-spend policy errors."""


class BusinessAdSpendPolicyNotFound(BusinessAdSpendError):
    """No authoritative policy exists for the requested business/channel/slug."""

    def __init__(self, business_slug: str, channel: str, slug: str) -> None:
        self.business_slug = business_slug
        self.channel = channel
        self.slug = slug
        super().__init__(f"business_ad_spend_policy_not_found:{business_slug}:{channel}:{slug}")


@dataclass(frozen=True)
class BusinessAdSpendPolicy:
    business_slug: str
    channel: str
    slug: str
    reservation_key: str
    reserved_credits: int
    daily_budget_cents: int
    total_budget_cents: int
    start_at: object
    end_at: object
    provider_account_id: str | None
    provider_campaign_id: str | None
    provider_group_id: str | None
    provider_ad_id: str | None
    provider_post_id: str | None
    status: str
    last_synced_spend_cents: int
    settled_credits: int
    metadata: dict
    created_at: object
    updated_at: object


_POLICY_COLUMNS = (
    "business_slug, channel, slug, reservation_key, reserved_credits, daily_budget_cents, "
    "total_budget_cents, start_at, end_at, provider_account_id, provider_campaign_id, "
    "provider_group_id, provider_ad_id, provider_post_id, status, last_synced_spend_cents, "
    "settled_credits, metadata, created_at, updated_at"
)


def _row_get(row, key: str, index: int):
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


def _json_dumps(value) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _policy_from_row(row) -> BusinessAdSpendPolicy:
    return BusinessAdSpendPolicy(
        business_slug=str(_row_get(row, "business_slug", 0)),
        channel=str(_row_get(row, "channel", 1)),
        slug=str(_row_get(row, "slug", 2)),
        reservation_key=str(_row_get(row, "reservation_key", 3)),
        reserved_credits=int(_row_get(row, "reserved_credits", 4)),
        daily_budget_cents=int(_row_get(row, "daily_budget_cents", 5)),
        total_budget_cents=int(_row_get(row, "total_budget_cents", 6)),
        start_at=_row_get(row, "start_at", 7),
        end_at=_row_get(row, "end_at", 8),
        provider_account_id=(
            None if _row_get(row, "provider_account_id", 9) is None else str(_row_get(row, "provider_account_id", 9))
        ),
        provider_campaign_id=(
            None if _row_get(row, "provider_campaign_id", 10) is None else str(_row_get(row, "provider_campaign_id", 10))
        ),
        provider_group_id=(
            None if _row_get(row, "provider_group_id", 11) is None else str(_row_get(row, "provider_group_id", 11))
        ),
        provider_ad_id=(
            None if _row_get(row, "provider_ad_id", 12) is None else str(_row_get(row, "provider_ad_id", 12))
        ),
        provider_post_id=(
            None if _row_get(row, "provider_post_id", 13) is None else str(_row_get(row, "provider_post_id", 13))
        ),
        status=str(_row_get(row, "status", 14)),
        last_synced_spend_cents=int(_row_get(row, "last_synced_spend_cents", 15)),
        settled_credits=int(_row_get(row, "settled_credits", 16)),
        metadata=_row_get(row, "metadata", 17) if isinstance(_row_get(row, "metadata", 17), dict) else {},
        created_at=_row_get(row, "created_at", 18),
        updated_at=_row_get(row, "updated_at", 19),
    )


def get_policy(conn, business_slug: str, channel: str, slug: str) -> BusinessAdSpendPolicy:
    row = conn.execute(
        f"""
        select {_POLICY_COLUMNS}
        from business_ad_spend_policies
        where business_slug = %s and channel = %s and slug = %s
        """,
        (business_slug, channel, slug),
    ).fetchone()
    if row is None:
        raise BusinessAdSpendPolicyNotFound(business_slug, channel, slug)
    return _policy_from_row(row)


def upsert_policy(
    conn,
    *,
    business_slug: str,
    channel: str,
    slug: str,
    reservation_key: str,
    reserved_credits: int,
    daily_budget_cents: int,
    total_budget_cents: int,
    start_at,
    end_at,
    provider_account_id: str | None = None,
    provider_campaign_id: str | None = None,
    provider_group_id: str | None = None,
    provider_ad_id: str | None = None,
    provider_post_id: str | None = None,
    status: str = "reserved",
    last_synced_spend_cents: int = 0,
    settled_credits: int = 0,
    metadata: dict | None = None,
) -> BusinessAdSpendPolicy:
    with conn.transaction():
        conn.execute(
            """
            insert into business_ad_spend_policies (
              business_slug, channel, slug, reservation_key, reserved_credits,
              daily_budget_cents, total_budget_cents, start_at, end_at,
              provider_account_id, provider_campaign_id, provider_group_id, provider_ad_id,
              provider_post_id, status, last_synced_spend_cents, settled_credits, metadata
            )
            values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s, %s::jsonb
            )
            on conflict (business_slug, channel, slug) do update set
              reservation_key = excluded.reservation_key,
              reserved_credits = excluded.reserved_credits,
              daily_budget_cents = excluded.daily_budget_cents,
              total_budget_cents = excluded.total_budget_cents,
              start_at = excluded.start_at,
              end_at = excluded.end_at,
              provider_account_id = excluded.provider_account_id,
              provider_campaign_id = excluded.provider_campaign_id,
              provider_group_id = excluded.provider_group_id,
              provider_ad_id = excluded.provider_ad_id,
              provider_post_id = excluded.provider_post_id,
              status = excluded.status,
              last_synced_spend_cents = excluded.last_synced_spend_cents,
              settled_credits = excluded.settled_credits,
              metadata = excluded.metadata,
              updated_at = now()
            """,
            (
                business_slug,
                channel,
                slug,
                reservation_key,
                int(reserved_credits),
                int(daily_budget_cents),
                int(total_budget_cents),
                start_at,
                end_at,
                provider_account_id,
                provider_campaign_id,
                provider_group_id,
                provider_ad_id,
                provider_post_id,
                status,
                int(last_synced_spend_cents),
                int(settled_credits),
                _json_dumps(metadata or {}),
            ),
        )
        return get_policy(conn, business_slug, channel, slug)


def update_policy(
    conn,
    *,
    business_slug: str,
    channel: str,
    slug: str,
    daily_budget_cents: int | None = None,
    end_at=None,
    provider_account_id: str | None = None,
    provider_campaign_id: str | None = None,
    provider_group_id: str | None = None,
    provider_ad_id: str | None = None,
    provider_post_id: str | None = None,
    status: str | None = None,
    last_synced_spend_cents: int | None = None,
    settled_credits: int | None = None,
    metadata_patch: dict | None = None,
) -> BusinessAdSpendPolicy:
    with conn.transaction():
        current = get_policy(conn, business_slug, channel, slug)
        merged_metadata = dict(current.metadata or {})
        if metadata_patch:
            merged_metadata.update(dict(metadata_patch))
        conn.execute(
            """
            update business_ad_spend_policies
            set daily_budget_cents = coalesce(%s, daily_budget_cents),
                end_at = coalesce(%s, end_at),
                provider_account_id = coalesce(%s, provider_account_id),
                provider_campaign_id = coalesce(%s, provider_campaign_id),
                provider_group_id = coalesce(%s, provider_group_id),
                provider_ad_id = coalesce(%s, provider_ad_id),
                provider_post_id = coalesce(%s, provider_post_id),
                status = coalesce(%s, status),
                last_synced_spend_cents = coalesce(%s, last_synced_spend_cents),
                settled_credits = coalesce(%s, settled_credits),
                metadata = %s::jsonb,
                updated_at = now()
            where business_slug = %s and channel = %s and slug = %s
            """,
            (
                daily_budget_cents,
                end_at,
                provider_account_id,
                provider_campaign_id,
                provider_group_id,
                provider_ad_id,
                provider_post_id,
                status,
                last_synced_spend_cents,
                settled_credits,
                _json_dumps(merged_metadata),
                business_slug,
                channel,
                slug,
            ),
        )
        return get_policy(conn, business_slug, channel, slug)
