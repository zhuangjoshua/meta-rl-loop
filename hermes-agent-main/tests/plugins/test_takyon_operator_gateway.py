from __future__ import annotations

from plugins.takyon.operator_gateway import _upstream_url


def test_upstream_url_rewrites_openai_style_paths_without_double_v1():
    target_url, query_params = _upstream_url(
        {"base_url": "https://api.openai.com/v1"},
        "/v1/chat/completions",
    )

    assert target_url == "https://api.openai.com/v1/chat/completions"
    assert query_params == []


def test_upstream_url_preserves_query_params_for_anthropic_style_routes():
    target_url, query_params = _upstream_url(
        {"base_url": "https://example.invalid/anthropic?api-version=2025-05-01"},
        "/v1/messages",
    )

    assert target_url == "https://example.invalid/anthropic/v1/messages"
    assert query_params == [("api-version", "2025-05-01")]
