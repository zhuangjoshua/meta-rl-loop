"""Regression tests for the takyon-reddit-ads skill metadata and guidance.

The Reddit ads runtime accepts either env-backed Safebox values or the saved
``$TAKYON_HOME/secrets/reddit_ads.json`` auth state. The skill frontmatter
must not reintroduce env-only readiness gates that falsely report setup as
missing, and the skill copy should not drift back to the old
``suppressed_test_mode`` operator guidance.
"""

from __future__ import annotations

from pathlib import Path


SKILL_MD = (
    Path(__file__).resolve().parents[2]
    / "skills/takyon/takyon-reddit-ads/SKILL.md"
)


def _parse_frontmatter(content: str) -> dict:
    from agent.skill_utils import parse_frontmatter

    fm, _ = parse_frontmatter(content)
    return fm


def test_reddit_ads_skill_does_not_declare_env_only_readiness_gates():
    content = SKILL_MD.read_text(encoding="utf-8")
    fm = _parse_frontmatter(content)

    assert fm.get("required_environment_variables") == []


def test_reddit_ads_skill_no_longer_mentions_suppressed_test_mode():
    content = SKILL_MD.read_text(encoding="utf-8")

    assert "suppressed_test_mode" not in content
    assert "Test-mode businesses need none of these" not in content
