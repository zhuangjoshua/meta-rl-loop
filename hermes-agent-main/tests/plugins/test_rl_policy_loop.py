"""Tests for the operator's policy improvement loop (rl_policy_loop.py)."""

from __future__ import annotations

import random

import pytest

from plugins.takyon.rl_policy_loop import (
    Arm, NoiseSchedule, PolicyStore, boldness, estimate_attribute_values,
    generate_candidates, improvement_step, sample_arm_attrs, select_policy,
)


def _arm(arm_id, attrs, roas, spend=20.0, wake=0):
    a = Arm(arm_id=arm_id, attrs=attrs, policy_version=0, policy_hash="seed",
            launched_wake=wake, spend=spend)
    a.measured_roas = roas
    return a


ATTRS_GOOD = {"kind": "video", "hook": "proof", "audience": "broad"}
ATTRS_BAD = {"kind": "image", "hook": "pain", "audience": "niche"}


def test_policy_store_lineage_and_hash_stability():
    store = PolicyStore()
    assert store.current.version == 0
    rng = random.Random(1)
    cands = generate_candidates(store, [], k=3, wake=1, rng=rng)
    adopted = store.adopt(cands[0])
    assert adopted.parent == 0
    assert store.current is adopted
    # hash is content-addressed and stable
    assert adopted.policy_hash == cands[0].policy_hash
    assert store.records()[0]["hash"] != adopted.policy_hash or (
        store.records()[0]["params"] == adopted.params and store.records()[0]["note"] == adopted.note)


def test_candidates_deterministic_for_same_seed():
    arms = [_arm("a", ATTRS_GOOD, 3.0), _arm("b", ATTRS_BAD, 0.5)]
    c1 = generate_candidates(PolicyStore(), arms, k=6, wake=2, rng=random.Random(42))
    c2 = generate_candidates(PolicyStore(), arms, k=6, wake=2, rng=random.Random(42))
    assert [c.params for c in c1] == [c.params for c in c2]
    assert [c.note for c in c1] == [c.note for c in c2]


def test_gradient_candidates_shift_weights_toward_measured_winner():
    arms = [_arm("a", ATTRS_GOOD, 4.0), _arm("b", ATTRS_BAD, 0.4)]
    store = PolicyStore()
    cands = generate_candidates(store, arms, k=3, wake=1, rng=random.Random(7))
    base_w = store.current.params["attr_weights"]["kind"]["video"]
    # EVERY candidate steps along the same gradient — all shift toward the winner
    assert all(c.params["attr_weights"]["kind"]["video"] > base_w for c in cands)
    # and step sizes are ordered: later candidates step further
    shifts = [c.params["attr_weights"]["kind"]["video"] for c in cands]
    assert shifts == sorted(shifts)


def test_noise_schedule_decays_to_floor():
    sched = NoiseSchedule(tau0=1.0, decay=0.5, floor=0.05)
    assert sched.tau(0) == 1.0
    assert sched.tau(1) == 0.5
    assert sched.tau(100) == 0.05


def test_candidates_span_a_boldness_spectrum():
    arms = [_arm("a", ATTRS_GOOD, 4.0, spend=100), _arm("b", ATTRS_BAD, 0.4, spend=100)]
    store = PolicyStore()
    cands = generate_candidates(store, arms, k=6, wake=5, rng=random.Random(3))
    scores = [boldness(store.current.params, c.params) for c in cands]
    # every candidate changes something, the set spans small -> bold, and the
    # ladder is monotone (same direction, increasing step size)
    assert all(s > 0 for s in scores)
    assert max(scores) > 2.5 * min(scores)
    assert scores == sorted(scores)


def test_hot_schedule_picks_bolder_than_cold_schedule():
    arms = [_arm("a", ATTRS_GOOD, 4.0, spend=100), _arm("b", ATTRS_BAD, 0.4, spend=100)]
    sched = NoiseSchedule()

    def mean_boldness(wake: int) -> float:
        total = 0.0
        for i in range(30):
            store = PolicyStore()
            cands = generate_candidates(store, arms, k=6, wake=wake, rng=random.Random(i))
            pick = select_policy(store, cands, wake=wake, rng=random.Random(1000 + i),
                                 schedule=sched)
            total += boldness(store.current.params, pick.params)
        return total / 30

    assert mean_boldness(0) > mean_boldness(60)   # hot -> bold swings, cold -> refinements


def test_cold_schedule_can_keep_the_incumbent():
    arms = [_arm("a", ATTRS_GOOD, 4.0, spend=100)]
    sched = NoiseSchedule()
    store = PolicyStore()
    cands = generate_candidates(store, arms, k=6, wake=200, rng=random.Random(2))
    picks = [select_policy(store, cands, wake=200, rng=random.Random(i), schedule=sched)
             for i in range(30)]
    # the incumbent sits at boldness 0 — at floor appetite it wins sometimes ("no change")
    assert any(p is store.current for p in picks)


def test_improvement_step_cuts_unprofitable_keeps_profitable_and_fills_slots():
    store = PolicyStore()
    good = _arm("good", ATTRS_GOOD, 3.5, spend=25.0)
    bad = _arm("bad", ATTRS_BAD, 0.3, spend=25.0)
    fresh = _arm("fresh", ATTRS_GOOD, 0.2, spend=2.0)  # under min spend: not judged
    result = improvement_step(store, [good, bad, fresh], [good, bad, fresh],
                              wake=3, rng=random.Random(9), schedule=NoiseSchedule())
    assert bad in result.cut and bad.status == "cut"
    assert good in result.kept and fresh in result.kept
    # freed slot refilled up to max_live_arms under the (possibly new) policy
    assert len(result.kept) + len(result.launched) == store.current.params["max_live_arms"]
    for arm in result.launched:
        assert arm.policy_version == store.current.version
        assert arm.policy_hash == store.current.policy_hash


def test_estimates_use_profitable_and_unprofitable_evidence():
    est = estimate_attribute_values([_arm("a", ATTRS_GOOD, 3.0), _arm("b", ATTRS_BAD, 0.5)])
    assert est["kind"]["video"][0] == pytest.approx(3.0)
    assert est["kind"]["image"][0] == pytest.approx(0.5)


def test_sample_arm_attrs_respects_weights():
    store = PolicyStore()
    params = store.current.params
    params["explore_share"] = 0.0
    params["attr_weights"]["kind"] = {"video": 1000.0, "image": 0.001}
    rng = random.Random(5)
    kinds = {sample_arm_attrs(params, rng)["kind"] for _ in range(20)}
    assert kinds == {"video"}
