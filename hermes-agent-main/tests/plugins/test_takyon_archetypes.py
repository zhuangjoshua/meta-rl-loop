"""Archetype registry (manifest key #3, the app|shopify|saas toggle) — pure-function tests.

No DB: archetypes.py is a pure leaf (registry + normalize + preset accessors + selectability gate);
get/set-against-a-connection are exercised by the PG-rig suite. These pins guard the registry's
invariants (readmodular.md §1, general-apps-plan.md §1: archetype = a named versioned PRESET, never
a code path; rollout gating is data on the preset) so a bad registry edit fails in CI, not on prod.
"""

from __future__ import annotations

import pytest

from plugins.takyon import archetypes as arch
from plugins.takyon import money_shape as ms


def test_registry_keys_match_declared_set():
    assert set(arch.BUSINESS_ARCHETYPES) == set(arch.ARCHETYPES)
    assert arch.DEFAULT_ARCHETYPE == arch.WEB_SAAS
    # Every preset's key is self-consistent and its default money shape is a real shape.
    for key, preset in arch.BUSINESS_ARCHETYPES.items():
        assert preset.key == key
        assert preset.version >= 1
        assert preset.label and preset.description
        assert preset.default_money_shape in ms.MONEY_SHAPES


def test_web_saas_is_the_enabled_identity_case():
    # Only web_saas is selectable today; the store/shopify pipelines are registered but gated
    # (readmodular §5 rollout). This is the zero-behavior-change guarantee.
    assert arch.is_enabled(arch.WEB_SAAS) is True
    assert arch.is_enabled(arch.MOBILE_APP) is False
    assert arch.is_enabled(arch.SHOPIFY_COMMERCE) is False
    assert tuple(p.key for p in arch.selectable_archetypes()) == (arch.WEB_SAAS,)
    # web_saas presets today's behavior byte-for-byte.
    web = arch.BUSINESS_ARCHETYPES[arch.WEB_SAAS]
    assert web.build_kind == "node_build"
    assert web.publish_adapter == "pointer_static"
    assert web.default_money_shape == ms.SUBSCRIPTION
    assert web.approval_gates == ()


def test_normalize_aliases_and_default():
    assert arch.normalize_archetype(None) == arch.WEB_SAAS
    assert arch.normalize_archetype("") == arch.WEB_SAAS
    assert arch.normalize_archetype("app") == arch.MOBILE_APP
    assert arch.normalize_archetype("iOS") == arch.MOBILE_APP
    assert arch.normalize_archetype("App-Store") == arch.MOBILE_APP
    assert arch.normalize_archetype("shopify") == arch.SHOPIFY_COMMERCE
    assert arch.normalize_archetype("store") == arch.SHOPIFY_COMMERCE
    assert arch.normalize_archetype("saas") == arch.WEB_SAAS
    assert arch.normalize_archetype("web_saas") == arch.WEB_SAAS


def test_normalize_unknown_and_required_raise():
    with pytest.raises(arch.InvalidArchetype):
        arch.normalize_archetype("blockchain-dapp")
    with pytest.raises(arch.InvalidArchetype):
        arch.normalize_archetype("", allow_empty=False)


def test_assert_selectable_fails_closed_on_disabled():
    assert arch.assert_selectable("saas") == arch.WEB_SAAS
    for disabled in ("app", "shopify"):
        with pytest.raises(arch.ArchetypeNotAvailable) as exc:
            arch.assert_selectable(disabled)
        # The gate token is the CEO's discovery surface.
        assert "archetype_unavailable:" in str(exc.value)


def test_default_money_shape_for_matches_preset():
    assert arch.default_money_shape_for("saas") == ms.SUBSCRIPTION
    assert arch.default_money_shape_for("app") == ms.SUBSCRIPTION
    assert arch.default_money_shape_for("shopify") == ms.COGS_PASSTHROUGH
    # Unknown falls back to the money-shape default rather than raising (create is defensive).
    assert arch.default_money_shape_for("nonsense") == ms.DEFAULT_MONEY_SHAPE


def test_preset_for_normalizes_and_rejects_unknown():
    assert arch.preset_for("app").key == arch.MOBILE_APP
    with pytest.raises(arch.InvalidArchetype):
        arch.preset_for("nope")


def test_check_constraint_values_match_registry():
    # The migration's CHECK pins exactly these three; keep the SQL and the registry in lockstep.
    assert set(arch.ARCHETYPES) == {"web_saas", "mobile_app", "shopify_commerce"}
