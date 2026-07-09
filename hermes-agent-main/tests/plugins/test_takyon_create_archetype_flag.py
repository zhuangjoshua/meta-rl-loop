"""/create --archetype toggle (readmodular §1.2) — parser + shell-argv routing tests.

Pins: the flag parses and normalizes aliases; a known-but-disabled archetype fails closed at the
SHELL with the `archetype_unavailable:<key>` gate token (immediate operator feedback — the store op
re-validates as the authoritative gate); an unknown value fails with the choice list; absent flag →
None (the DB default stays authoritative); the interactive shell tokenizer routes `--archetype` as
a value flag so the brief text never swallows it.
"""

from __future__ import annotations

import pytest

from plugins.takyon import cli
from plugins.takyon import archetypes as arch

USAGE = "usage: test-create"


def _parse(argv):
    return cli._parse_business_start_args(argv, usage=USAGE, auto_default=True)


def test_absent_flag_yields_none_archetype():
    slug, name, goal, mode, sched, auto, no_auto, follow, detach, archetype, animations = _parse(
        ["create", "acme", "build a thing"]
    )
    assert slug == "acme"
    assert archetype is None  # absent → store/DB default (web_saas) stays authoritative


def test_explicit_saas_parses_and_normalizes():
    *_head, archetype, _animations = _parse(["create", "--archetype", "saas", "acme", "goal text"])
    assert archetype == arch.WEB_SAAS


def test_alias_normalization_via_flag():
    *_head, archetype, _animations = _parse(["create", "--archetype", "web_saas", "acme", "goal"])
    assert archetype == arch.WEB_SAAS


def test_enabled_mobile_app_parses_through_the_flag():
    *_head, archetype, _animations = _parse(["create", "--archetype", "app", "acme", "goal"])
    assert archetype == arch.MOBILE_APP


def test_disabled_archetype_fails_closed_with_gate_token():
    with pytest.raises(SystemExit) as exc:
        _parse(["create", "--archetype", "shopify", "acme", "goal"])
    assert "archetype_unavailable:" in str(exc.value)


def test_unknown_archetype_fails_with_choices():
    with pytest.raises(SystemExit) as exc:
        _parse(["create", "--archetype", "metaverse", "acme", "goal"])
    assert "unknown archetype" in str(exc.value)


def test_flag_requires_value():
    with pytest.raises(SystemExit):
        _parse(["create", "--archetype"])


def test_shell_create_argv_routes_archetype_as_value_flag():
    argv = cli._shell_create_argv("create", '--archetype saas acme brief text here')
    # --archetype and its value must be consumed as flag+value, not folded into the brief.
    assert argv[:3] == ["create", "--archetype", "saas"]
    parsed = _parse(argv)
    assert parsed[-2] == arch.WEB_SAAS  # archetype is second-to-last (animations is the last element)


def test_shell_create_argv_archetype_with_slug_flag():
    argv = cli._shell_create_argv("create", '--archetype saas --slug acme the whole brief')
    assert "--archetype" in argv and "--slug" in argv
    slug, *_mid, archetype, _animations = _parse(argv)
    assert slug == "acme"
    assert archetype == arch.WEB_SAAS


def test_animation_flag_sets_last_element():
    # --animation is opt-in and rides as the last tuple element; absent → False.
    *_head, animations = _parse(["create", "--animation", "acme", "goal text"])
    assert animations is True
    *_head2, animations_off = _parse(["create", "acme", "goal text"])
    assert animations_off is False
    # alias
    *_head3, animations_alias = _parse(["create", "--animations", "acme", "goal"])
    assert animations_alias is True


def test_animation_flag_adds_landing_hero_directive_only_when_set():
    from plugins.takyon.turn_runtime import _business_bootstrap_instruction

    on = _business_bootstrap_instruction("acme", "build a thing", "live", animations=True)
    off = _business_bootstrap_instruction("acme", "build a thing", "live")
    assert "Landing hero animation" in on
    assert "framer-motion" in on
    assert "Landing hero animation" not in off  # no-flag prose is unchanged
