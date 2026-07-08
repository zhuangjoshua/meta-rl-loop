"""Unit contract for the public <title> derivation guard (no DB needed)."""


def test_starter_title_rejects_slug_embedded_strategy_headings():
    """The public <title> must never be slug-derived. CEO strategy docs are routinely headed
    '<slug> Strategy (Brief)'; the generic-title guard must reject any title still containing
    the internal slug token so derivation falls through to worker-authored hero copy."""
    from plugins.takyon import core

    for junk in ("qaproof0708b Strategy", "acceptinvoice0708 strategy", "Acceptmeal0708 Strategy Brief"):
        slug = junk.split()[0].lower()
        assert core._starter_title_is_generic(junk, slug=slug) is True
    # Real value-prop titles (no slug token) must pass.
    assert core._starter_title_is_generic(
        "Turn a topic into a finished slide deck in minutes.", slug="magicslides"
    ) is False
    # Short common-word slugs only reject titles containing the exact token.
    assert core._starter_title_is_generic("The best test-prep app", slug="test") is True
    assert core._starter_title_is_generic("The greatest prep app", slug="test") is False


def test_sanitize_starter_title_strips_slug_and_bounds_length():
    """Final-candidate hygiene: a slug-embedded, paragraph-length value prop (paylane0708
    regression) is salvaged into a bounded, slug-free clause; clean titles pass through."""
    from plugins.takyon import core

    t = core._sanitize_starter_title(
        "paylane0708 helps freelancers turn a client payment request into a shareable link "
        "and a simple paid/unpaid view, so they can spend less time chasing status.",
        slug="paylane0708",
    )
    assert "paylane0708" not in t.lower()
    assert len(t) <= 90
    assert t[0].isupper()
    clean = "Fair weekly rotas without the manager back-and-forth."
    assert core._sanitize_starter_title(clean, slug="tidyrota0708") == clean
