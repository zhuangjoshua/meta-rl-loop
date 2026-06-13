"""Regression tests for the takyon-reddit skill metadata and guidance."""

from __future__ import annotations

from pathlib import Path


SKILL_MD = (
    Path(__file__).resolve().parents[2]
    / "skills/takyon/takyon-reddit/SKILL.md"
)


def _parse_frontmatter(content: str) -> dict:
    from agent.skill_utils import parse_frontmatter

    fm, _ = parse_frontmatter(content)
    return fm


def test_reddit_skill_does_not_declare_env_only_readiness_gates():
    content = SKILL_MD.read_text(encoding="utf-8")
    fm = _parse_frontmatter(content)

    assert fm.get("required_environment_variables") == []


def test_reddit_skill_routes_paid_work_to_reddit_ads():
    content = SKILL_MD.read_text(encoding="utf-8")

    assert "takyon-reddit-ads" in content
    assert "Paid Reddit execution belongs to `takyon-reddit-ads`" in content
