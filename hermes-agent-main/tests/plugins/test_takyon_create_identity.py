from plugins.takyon.cli import _business_bootstrap_instruction, _resolve_create_identity


def test_resolve_create_identity_prefers_name_derived_from_goal_text():
    name, slug = _resolve_create_identity("", "build Longer - a men's health app")

    assert name == "Longer"
    assert slug == "longer"


def test_resolve_create_identity_falls_back_to_humanized_slug_hint():
    name, slug = _resolve_create_identity("", "", "coachesyard")

    assert name == "Coachesyard"
    assert slug == "coachesyard"


def test_bootstrap_prompt_pins_canonical_business_name():
    prompt = _business_bootstrap_instruction(
        "longer",
        "a men's health app",
        "live",
        business_name="Longer",
    )

    assert "Canonical business name: Longer" in prompt
    assert "Use exactly this business name on the first pass." in prompt
