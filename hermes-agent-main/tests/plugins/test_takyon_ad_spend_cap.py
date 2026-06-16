"""Ad-spend cap + gateway-boundary regression tests (AUTH0.md section 8).

Covers the two pure behaviours landed for the ad-spend red-team fix:
1. `business_ad_spend.enforce_daily_budget` — the single cap implementation shared by the
   control tool and the runtime gateway (no DB / no network).
2. `creative_gateway._require_internal_session` — the creative/ad gateway is localhost-only:
   any request that transited the public reverse proxy (carries `X-Forwarded-*`) is refused,
   so it cannot be reached from `app.fourmanifold.com` even with the shared dashboard token.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from plugins.takyon import business_ad_spend as spend

NOW = datetime(2026, 6, 15, tzinfo=timezone.utc)


def _policy(total_cents, spent_cents, days):
    # $total reserved (1 credit = 1 cent), $spent already synced, window `days` days out.
    return SimpleNamespace(
        total_budget_cents=total_cents,
        last_synced_spend_cents=spent_cents,
        end_at=NOW + timedelta(days=days),
    )


def test_enforce_daily_budget_allows_within_reserved_window():
    # $10/day x 5 days = $50, exactly the $50 reserved cap -> allowed.
    spend.enforce_daily_budget(_policy(5000, 0, 5), daily_budget_cents=1000, now=NOW)


def test_enforce_daily_budget_blocks_over_window():
    # $11/day x 5 days = $55 > $50 reserved -> blocked.
    with pytest.raises(spend.AdSpendCapExceeded):
        spend.enforce_daily_budget(_policy(5000, 0, 5), daily_budget_cents=1100, now=NOW)


def test_enforce_daily_budget_blocks_over_remaining():
    # $60 one-day request > $50 remaining -> blocked.
    with pytest.raises(spend.AdSpendCapExceeded):
        spend.enforce_daily_budget(_policy(5000, 0, 5), daily_budget_cents=6000, now=NOW)


def test_enforce_daily_budget_blocks_over_safety_cap():
    with pytest.raises(spend.AdSpendCapExceeded):
        spend.enforce_daily_budget(
            _policy(5000, 0, 5), daily_budget_cents=1000, now=NOW, safety_cap_cents=500
        )


def test_enforce_daily_budget_blocks_non_positive():
    with pytest.raises(spend.AdSpendCapExceeded):
        spend.enforce_daily_budget(_policy(5000, 0, 5), daily_budget_cents=0, now=NOW)


def test_enforce_daily_budget_uses_already_synced_spend():
    # $40 already spent -> only $10 remaining; a $20/day raise is blocked.
    with pytest.raises(spend.AdSpendCapExceeded):
        spend.enforce_daily_budget(_policy(5000, 4000, 5), daily_budget_cents=2000, now=NOW)


def test_internal_session_rejects_proxied_request():
    from fastapi import HTTPException

    from plugins.takyon import creative_gateway as gw

    proxied = SimpleNamespace(headers={"x-forwarded-host": "app.fourmanifold.com"})
    with pytest.raises(HTTPException) as exc:
        gw._require_internal_session(proxied, session_token="anything")
    assert exc.value.status_code == 404


# --- _assert_ad_set_budget_authorized: the actual gateway-reachable gate ----------------
# This is what creative_gateway set_budget calls before mutating live spend. It loads the
# funded policy, refuses a target object that isn't this business's (cross-business IDOR),
# then runs the shared cap. We stub ONLY the DB read so the IDOR check, the real
# `enforce_daily_budget`, and the real TakyonError mapping all run for real (no PG rig).


def _authz_policy(*, group_id="ag_real", campaign_id="cmp_real", total_cents=100000, spent_cents=0, days=30):
    return SimpleNamespace(
        provider_group_id=group_id,
        provider_campaign_id=campaign_id,
        total_budget_cents=total_cents,
        last_synced_spend_cents=spent_cents,
        end_at=NOW + timedelta(days=days),
    )


def _core_with_policy(monkeypatch, policy):
    from plugins.takyon import core

    if policy is None:
        from plugins.takyon import business_ad_spend as spend_backend

        def _raise(business, *, channel, slug):
            raise spend_backend.BusinessAdSpendPolicyNotFound(business, channel, slug)

        monkeypatch.setattr(core, "_load_ad_spend_policy", _raise)
    else:
        monkeypatch.setattr(core, "_load_ad_spend_policy", lambda business, *, channel, slug: policy)
    return core


def test_authorizer_allows_authorized_target_within_cap(monkeypatch):
    core = _core_with_policy(monkeypatch, _authz_policy())
    core._assert_ad_set_budget_authorized(
        channel="meta", business="acme", slug="default", target_id="ag_real", daily_budget_cents=1000
    )


def test_authorizer_blocks_cross_business_target(monkeypatch):
    # Same cap room, but the ad set being raised belongs to a different campaign -> refused.
    core = _core_with_policy(monkeypatch, _authz_policy())
    with pytest.raises(core.TakyonError):
        core._assert_ad_set_budget_authorized(
            channel="meta", business="acme", slug="default", target_id="ag_someone_else", daily_budget_cents=1000
        )


def test_authorizer_blocks_over_cap(monkeypatch):
    # Authorized target, but $600/day > $500 reserved remaining -> refused via shared cap.
    core = _core_with_policy(monkeypatch, _authz_policy(total_cents=50000))
    with pytest.raises(core.TakyonError):
        core._assert_ad_set_budget_authorized(
            channel="meta", business="acme", slug="default", target_id="ag_real", daily_budget_cents=60000
        )


def test_authorizer_blocks_missing_policy(monkeypatch):
    # No funded campaign for this (business, channel, slug) -> refused (can't spend unreserved).
    core = _core_with_policy(monkeypatch, None)
    with pytest.raises(core.TakyonError):
        core._assert_ad_set_budget_authorized(
            channel="meta", business="acme", slug="default", target_id="ag_real", daily_budget_cents=1000
        )


def test_authorizer_requires_business_and_slug():
    from plugins.takyon import core

    with pytest.raises(core.TakyonError):
        core._assert_ad_set_budget_authorized(
            channel="meta", business="", slug="default", target_id="ag_real", daily_budget_cents=1000
        )
