from __future__ import annotations

import httpx

from plugins.takyon.operator_gateway import _response_headers, _upstream_url


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


def test_response_headers_strip_content_encoding_for_decoded_body():
    headers = httpx.Headers(
        {
            "content-encoding": "gzip",
            "content-length": "123",
            "content-type": "application/json",
            "x-request-id": "req_123",
        }
    )

    result = _response_headers(headers, streaming=False)

    assert "content-encoding" not in {key.lower() for key in result}
    assert "content-length" not in {key.lower() for key in result}
    assert result["content-type"] == "application/json"
    assert result["x-request-id"] == "req_123"


def test_response_headers_strip_content_encoding_for_streamed_body():
    headers = httpx.Headers(
        {
            "Content-Encoding": "gzip",
            "Transfer-Encoding": "chunked",
            "Content-Type": "text/event-stream",
        }
    )

    result = _response_headers(headers, streaming=True)

    lowered = {key.lower() for key in result}
    assert "content-encoding" not in lowered
    assert "transfer-encoding" not in lowered
    assert result["content-type"] == "text/event-stream"
