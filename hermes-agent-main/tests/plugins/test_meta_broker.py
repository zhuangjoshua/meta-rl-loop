"""Regression tests for the official Meta Ads MCP/Safebox seam."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from plugins.takyon import composio_distribution, meta_graph, meta_mcp, safebox_app

_TOKEN = "secret-internal-token"


def test_metaads_proxy_request_is_disabled():
    with pytest.raises(
        composio_distribution.ComposioDistributionError,
        match="disabled",
    ):
        composio_distribution.metaads_proxy_request(
            method="POST",
            endpoint="https://graph.facebook.com/v23.0/act_123/adcreatives",
            connected_account_id="conn_metaads_123",
            body={"name": "Demo creative"},
        )


def test_meta_mcp_auth_error_detects_taskgroup_wrapped_401():
    class _Response:
        status_code = 401

    class _HTTPError(Exception):
        response = _Response()

    wrapped = ExceptionGroup("streamable http task group", [_HTTPError("unauthorized")])

    assert meta_mcp._auth_error(wrapped) is True


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(safebox_app._SAFEBOX_TOKEN_ENV, _TOKEN)
    return TestClient(safebox_app.build_safebox_app())


def _auth():
    return {"Authorization": f"Bearer {_TOKEN}"}


def test_meta_config_route_redacts_token(client, monkeypatch):
    values = {
        "META_GRAPH_VERSION": "v21.0",
        "META_SYSTEM_USER_ACCESS_TOKEN": "real-system-user-token-that-must-not-egress",
        "META_MCP_OAUTH_TOKEN": "real-mcp-token-that-must-not-egress",
        "META_AD_ACCOUNT_ID": "act_1300104788312342",
        "META_PAGE_ID": "page_123",
        "META_INSTAGRAM_ID": "ig_456",
    }

    monkeypatch.setattr(
        safebox_app.safebox,
        "first_env_backed_value",
        lambda *keys: next((values[key] for key in keys if values.get(key)), ""),
    )

    resp = client.post("/v1/providers/meta/config", headers=_auth())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["token"] == ""
    assert data["has_token"] is True
    assert "real-system-user-token-that-must-not-egress" not in resp.text
    assert data["version"] == "v21.0"
    assert data["ad_account_id"] == "act_1300104788312342"
    assert data["page_id"] == "page_123"
    assert data["instagram_user_id"] == "ig_456"
    assert data["has_mcp_oauth_token"] is True
    assert data["mcp_endpoint"] == "https://mcp.facebook.com/ads"


def test_meta_config_route_requires_internal_token(client):
    resp = client.post("/v1/providers/meta/config")
    assert resp.status_code == 401


def test_meta_mcp_call_route_brokers_key_free_tool_result(client, monkeypatch):
    captured = {}

    def fake_meta_mcp_call(*, tool_name, arguments=None, timeout=60.0):
        captured["call"] = (tool_name, arguments, timeout)
        return {"id": "campaign-123"}

    monkeypatch.setattr(safebox_app.safebox, "meta_mcp_call", fake_meta_mcp_call)

    resp = client.post(
        "/v1/providers/meta/mcp/call",
        headers=_auth(),
        json={
            "tool_name": "ads_create_campaign",
            "arguments": {"ad_account_id": "act_123", "name": "Demo"},
            "timeout": 12.0,
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": "campaign-123"}
    assert captured["call"] == (
        "ads_create_campaign",
        {"ad_account_id": "act_123", "name": "Demo"},
        12.0,
    )


def test_meta_graph_route_brokers_graph_call_without_egressing_token(client, monkeypatch):
    values = {
        "META_GRAPH_VERSION": "v21.0",
        "META_SYSTEM_USER_ACCESS_TOKEN": "local-graph-token",
    }
    captured = {}

    monkeypatch.setattr(
        safebox_app.safebox,
        "first_env_backed_value",
        lambda *keys: next((values[key] for key in keys if values.get(key)), ""),
    )

    def fake_graph(method, path, params, *, token, version, host, timeout):
        captured["call"] = (method, path, params, token, version, host, timeout)
        return {"id": "graph-ok"}

    monkeypatch.setattr(meta_graph, "_graph", fake_graph)

    resp = client.post(
        "/v1/providers/meta/graph",
        headers=_auth(),
        json={
            "method": "GET",
            "path": "/me",
            "params": {"fields": "id,name"},
            "host": "graph.facebook.com",
            "timeout": 15,
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": "graph-ok"}
    assert captured["call"] == (
        "GET",
        "me",
        {"fields": "id,name"},
        "local-graph-token",
        "v21.0",
        "graph.facebook.com",
        15.0,
    )


def test_meta_graph_route_requires_system_user_token(client, monkeypatch):
    monkeypatch.setattr(safebox_app.safebox, "first_env_backed_value", lambda *keys: "")

    resp = client.post(
        "/v1/providers/meta/graph",
        headers=_auth(),
        json={"method": "GET", "path": "/me", "params": {}},
    )

    assert resp.status_code == 502
    assert "META_SYSTEM_USER_ACCESS_TOKEN" in resp.text


def test_meta_graph_upload_image_route_brokers_bytes_without_egressing_token(client, monkeypatch):
    values = {
        "META_GRAPH_VERSION": "v21.0",
        "META_SYSTEM_USER_ACCESS_TOKEN": "local-graph-token",
    }
    captured = {}

    monkeypatch.setattr(
        safebox_app.safebox,
        "first_env_backed_value",
        lambda *keys: next((values[key] for key in keys if values.get(key)), ""),
    )

    def fake_upload_image(token, ad_account_id, image_bytes, *, name, version="v21.0", timeout=180.0):
        captured["call"] = (token, ad_account_id, image_bytes, name, version, timeout)
        return {"hash": "image-hash-1", "url": "https://cdn.example/img.png"}

    monkeypatch.setattr(meta_graph, "upload_image", fake_upload_image)

    resp = client.post(
        "/v1/providers/meta/graph/upload-image",
        headers=_auth(),
        json={
            "ad_account_id": "act_123",
            "name": "demo-image",
            "data_b64": "ZmFrZS1pbWFnZS1ieXRlcw==",
            "timeout": 22,
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["hash"] == "image-hash-1"
    assert captured["call"] == (
        "local-graph-token",
        "act_123",
        b"fake-image-bytes",
        "demo-image",
        "v21.0",
        22.0,
    )


def test_meta_graph_upload_video_route_brokers_bytes_without_egressing_token(client, monkeypatch):
    values = {
        "META_GRAPH_VERSION": "v21.0",
        "META_SYSTEM_USER_ACCESS_TOKEN": "local-graph-token",
    }
    captured = {}

    monkeypatch.setattr(
        safebox_app.safebox,
        "first_env_backed_value",
        lambda *keys: next((values[key] for key in keys if values.get(key)), ""),
    )

    def fake_upload_video(token, ad_account_id, video_bytes, *, name, version="v21.0", poll=True, timeout=180.0):
        captured["call"] = (token, ad_account_id, video_bytes, name, version, poll, timeout)
        return "video-123"

    monkeypatch.setattr(meta_graph, "upload_video", fake_upload_video)

    resp = client.post(
        "/v1/providers/meta/graph/upload-video",
        headers=_auth(),
        json={
            "ad_account_id": "act_123",
            "name": "demo-video",
            "data_b64": "ZmFrZS12aWRlby1ieXRlcw==",
            "poll": False,
            "timeout": 33,
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"video_id": "video-123"}
    assert captured["call"] == (
        "local-graph-token",
        "act_123",
        b"fake-video-bytes",
        "demo-video",
        "v21.0",
        False,
        33.0,
    )


def test_meta_graph_ensure_custom_conversion_route_brokers_key_free_result(client, monkeypatch):
    values = {
        "META_GRAPH_VERSION": "v21.0",
        "META_SYSTEM_USER_ACCESS_TOKEN": "local-graph-token",
    }
    captured = {}

    monkeypatch.setattr(
        safebox_app.safebox,
        "first_env_backed_value",
        lambda *keys: next((values[key] for key in keys if values.get(key)), ""),
    )

    def fake_ensure(token, ad_account_id, *, name, rule, custom_event_type, version="v21.0"):
        captured["call"] = (token, ad_account_id, name, rule, custom_event_type, version)
        return {"id": "cc-123", "existed": True}

    monkeypatch.setattr(meta_graph, "ensure_custom_conversion", fake_ensure)

    resp = client.post(
        "/v1/providers/meta/graph/ensure-custom-conversion",
        headers=_auth(),
        json={
            "ad_account_id": "act_123",
            "name": "demo-cc",
            "rule": "{\"url\":{\"i_contains\":\"demo\"}}",
            "custom_event_type": "LEAD",
            "timeout": 18,
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": "cc-123", "existed": True}
    assert captured["call"] == (
        "local-graph-token",
        "act_123",
        "demo-cc",
        "{\"url\":{\"i_contains\":\"demo\"}}",
        "LEAD",
        "v21.0",
    )


def test_meta_graph_route_is_registered(client):
    paths = {route.path for route in safebox_app.build_safebox_app().routes}
    assert "/v1/providers/meta/config" in paths
    assert "/v1/providers/meta/mcp/call" in paths
    assert "/v1/providers/meta/mcp/tools" in paths
    assert "/v1/providers/meta/graph" in paths
    assert "/v1/providers/meta/graph/upload-image" in paths
    assert "/v1/providers/meta/graph/upload-video" in paths
    assert "/v1/providers/meta/graph/ensure-custom-conversion" in paths
