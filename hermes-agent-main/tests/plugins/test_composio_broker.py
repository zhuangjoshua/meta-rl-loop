"""Regression tests for the Composio distribution broker seam.

COMPOSIO_API_KEY is a provider secret held by the safebox and denied /v1/env egress, so a runtime
plane cannot resolve it. ``composio_distribution._request`` must broker the WHOLE call through the
safebox when on a runtime plane (``_use_remote_authority()`` True), and call Composio directly only
on the safebox host. Because every channel (twitter/reddit/reddit_ads/metaads) and the
connected-account lookup funnel through ``_request``, this one seam covers them all.
"""

import pytest

from plugins.takyon import composio_distribution as cd


def _boom(*_a, **_k):
    raise AssertionError("must not be called on this plane")


def test_request_brokers_through_safebox_when_remote(monkeypatch):
    captured = {}

    monkeypatch.setattr(cd.safebox, "_use_remote_authority", lambda: True)

    def fake_forward(*, method, path, json_body=None, params=None, timeout=60.0):
        captured["call"] = (method, path, json_body, params)
        return {"items": []}

    monkeypatch.setattr(cd.safebox, "composio_forward", fake_forward)
    # On a runtime plane the key must NOT be read and Composio must NOT be hit directly.
    monkeypatch.setattr(cd, "_api_key", _boom)
    monkeypatch.setattr(cd, "_load_httpx", _boom)

    out = cd._request("GET", "connected_accounts", params=[("toolkit_slugs", "twitter")])

    assert out == {"items": []}
    method, path, json_body, params = captured["call"]
    assert method == "GET" and path == "connected_accounts"
    # params normalized to JSON-safe list-of-lists for transport
    assert params == [["toolkit_slugs", "twitter"]]


def test_request_direct_when_local_on_safebox_host(monkeypatch):
    monkeypatch.setattr(cd.safebox, "_use_remote_authority", lambda: False)
    # On the safebox host the broker client must NOT be used (would recurse).
    monkeypatch.setattr(cd.safebox, "composio_forward", _boom)
    monkeypatch.setattr(cd, "_api_key", lambda: "local-key")
    monkeypatch.setattr(cd, "_base_url", lambda: "https://backend.composio.dev/api/v3.1")

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True}

    class _HX:
        def request(self, *a, **k):
            # the locally-resolved key rides the outbound header, never the broker
            assert k["headers"]["x-api-key"] == "local-key"
            return _Resp()

    monkeypatch.setattr(cd, "_load_httpx", lambda: _HX())

    out = cd._request("GET", "connected_accounts")
    assert out == {"ok": True}
