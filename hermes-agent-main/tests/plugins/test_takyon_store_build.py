"""EAS build-lane money-safety (plugins/takyon/store_build.py) — the settle-at-trigger boundary.

The load-bearing money rule: an EAS build spends real money at the TRIGGER (Expo accepts the
upload), not at build completion. These pins prove reserve→settle-at-trigger→release ordering with a
mocked invoker (no real build needed), plus the fail-closed default invoker. This is what makes the
build lane safe to ship before the real builder/eas-credentials exist.
"""

from __future__ import annotations

import pytest

from plugins.takyon import store_build as sb


class _Recorder:
    def __init__(self):
        self.calls = []

    def reserve(self, business, credits, key):
        self.calls.append(("reserve", business, credits, key))
        return {"reservation_key": key}

    def settle(self, key, actual, meta):
        self.calls.append(("settle", key, actual, meta))

    def release(self, key, meta):
        self.calls.append(("release", key, meta))


def test_success_reserves_then_settles_at_trigger_never_releases():
    r = _Recorder()
    result = sb.run_build(
        business_slug="acme",
        lane="preview",
        credits=2,
        reservation_key="rk-1",
        reserve=r.reserve,
        settle=r.settle,
        release=r.release,
        invoke_eas=lambda: sb.BuildResult(build_id="b-123", lane="preview", actual_credits=2),
    )
    assert result.build_id == "b-123"
    kinds = [c[0] for c in r.calls]
    assert kinds == ["reserve", "settle"]  # settle at trigger; NO release
    assert r.calls[1][1] == "rk-1" and r.calls[1][2] == 2  # settled actual credits
    assert r.calls[1][3]["build_id"] == "b-123"


def test_trigger_failure_releases_never_settles_and_reraises():
    r = _Recorder()

    def boom():
        raise sb.EasBuilderUnconfigured("eas_builder_unconfigured: not provisioned")

    with pytest.raises(sb.EasBuilderUnconfigured):
        sb.run_build(
            business_slug="acme",
            lane="preview",
            credits=2,
            reservation_key="rk-2",
            reserve=r.reserve,
            settle=r.settle,
            release=r.release,
            invoke_eas=boom,
        )
    kinds = [c[0] for c in r.calls]
    assert kinds == ["reserve", "release"]  # released (no spend happened); NO settle
    assert r.calls[1][2]["error"].startswith("eas_builder_unconfigured")


def test_settle_defaults_to_reserved_credits_when_actual_absent():
    r = _Recorder()
    sb.run_build(
        business_slug="acme",
        lane="production",
        credits=3,
        reservation_key="rk-3",
        reserve=r.reserve,
        settle=r.settle,
        release=r.release,
        invoke_eas=lambda: sb.BuildResult(build_id="b-9", lane="production", actual_credits=None),
    )
    settle = next(c for c in r.calls if c[0] == "settle")
    assert settle[2] == 3  # falls back to the reserved amount


def test_default_invoker_fails_closed_before_any_spend():
    r = _Recorder()
    invoker = sb.default_eas_invoker(business_slug="acme", lane="preview", expo_token="tok")
    with pytest.raises(sb.EasBuilderUnconfigured) as exc:
        sb.run_build(
            business_slug="acme",
            lane="preview",
            credits=2,
            reservation_key="rk-4",
            reserve=r.reserve,
            settle=r.settle,
            release=r.release,
            invoke_eas=invoker,
        )
    assert "eas_builder_unconfigured" in str(exc.value)
    # Reserved then released — nothing settled, no money stranded.
    assert [c[0] for c in r.calls] == ["reserve", "release"]


def test_negative_credits_refused_before_reserve():
    r = _Recorder()
    with pytest.raises(sb.StoreBuildError):
        sb.run_build(
            business_slug="acme",
            lane="preview",
            credits=-1,
            reservation_key="rk-5",
            reserve=r.reserve,
            settle=r.settle,
            release=r.release,
            invoke_eas=lambda: sb.BuildResult(build_id="x", lane="preview"),
        )
    assert r.calls == []  # nothing reserved
