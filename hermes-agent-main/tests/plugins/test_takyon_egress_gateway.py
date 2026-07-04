"""Hostile-subuser attack coverage for the credentialed-egress gateway (delta 6).

Each test maps to a red-team attack from egress-rail-build-spec.md. Subusers author the action
code and choose method/path/headers/body/query but NOT the host/credential/placement. Pure unit
tests (no network) over the build/validation layer — the load-bearing security surface.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from plugins.takyon import egress_gateway as eg


def _conn(host="api.stripe.com", methods=("GET", "POST"), prefix=None, placement=None):
    return eg.ProviderConnection(
        id="1", business_slug="b", connection_slug="s", provider_kind="stripe",
        allowed_host=host, allowed_path_prefix=prefix, allowed_methods=methods,
        placement=placement or {"type": "header", "name": "Authorization"},
        scope="business", status="active",
    )


def _build(**kw):
    base = dict(method="GET", path="/v1/x", query=None, headers=None, body=None, secret="sk_live_X")
    base.update(kw)
    return eg.build_request(_conn(kw.pop("_conn", _conn()) and _conn()), **base) if False else eg.build_request(
        kw.get("_conn") or _conn(), method=base["method"], path=base["path"], query=base["query"],
        headers=base["headers"], body=base["body"], secret=base["secret"],
    )


@pytest.mark.parametrize("path", ["@evil.attacker.com/collect", "//evil.com/x", "/x\\y", "noslash", "/x\r\nHost: evil"])
def test_url_authority_confusion_and_crlf_in_path_refused(path):
    with pytest.raises(eg.EgressError) as e:
        eg.build_request(_conn(), method="GET", path=path, query=None, headers=None, body=None, secret="k")
    assert e.value.code == "bad_path"


def test_crlf_in_query_and_header_refused():
    with pytest.raises(eg.EgressError) as e:
        eg.build_request(_conn(), method="GET", path="/x", query={"a": "b\r\nX: y"}, headers=None, body=None, secret="k")
    assert e.value.code == "bad_query"
    with pytest.raises(eg.EgressError) as e:
        eg.build_request(_conn(), method="GET", path="/x", query=None, headers={"accept": "a\r\nb"}, body=None, secret="k")
    assert e.value.code == "bad_header"


@pytest.mark.parametrize("host", [
    "137.184.75.57", "134.209.123.8", "206.81.10.173", "10.116.0.2", "app.fourmanifold.com",
    "x.coscale.app", "api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com",
    "api.tavily.com", "localhost", "foo.internal", "0.0.0.0",
])
def test_platform_self_metered_and_internal_hosts_denied(host):
    assert eg.host_denied_for_egress(host) is not None


@pytest.mark.parametrize("host", ["api.stripe.com", "api.github.com", "hooks.slack.com"])
def test_ordinary_third_party_hosts_allowed(host):
    assert eg.host_denied_for_egress(host) is None


@pytest.mark.parametrize("addr,blocked", [
    ("::ffff:127.0.0.1", True), ("::ffff:10.0.0.1", True), ("169.254.169.254", True),
    ("127.0.0.1", True), ("10.1.2.3", True), ("8.8.8.8", False), ("140.82.116.6", False),
])
def test_ip_guard_including_ipv4_mapped(addr, blocked):
    assert eg._blocked_ip(addr) is blocked


def test_method_and_path_prefix_enforced():
    with pytest.raises(eg.EgressError) as e:
        eg.build_request(_conn(methods=("GET",)), method="DELETE", path="/x", query=None, headers=None, body=None, secret="k")
    assert e.value.code == "method_not_allowed"
    with pytest.raises(eg.EgressError) as e:
        eg.build_request(_conn(prefix="/v1/"), method="GET", path="/v2/secret", query=None, headers=None, body=None, secret="k")
    assert e.value.code == "path_not_allowed"


@pytest.mark.parametrize("placement", [{"type": "query", "name": "api_key"}, {"type": "basic", "name": "u"}])
def test_query_and_basic_secret_placement_refused(placement):
    with pytest.raises(eg.EgressError) as e:
        eg.build_request(_conn(placement=placement), method="GET", path="/x", query=None, headers=None, body=None, secret="k")
    assert e.value.code == "unsupported_placement"


def test_header_smuggling_dropped_credential_and_host_forced():
    m, url, ip, hdrs, bb = eg.build_request(
        _conn(), method="GET", path="/v1/charges", query=None,
        headers={"authorization": "Bearer attacker", "host": "evil.com", "accept": "application/json", "cookie": "x=y"},
        body=None, secret="sk_live_REAL",
    )
    assert hdrs["Authorization"] == "sk_live_REAL"
    assert hdrs["host"] == "api.stripe.com"
    assert "cookie" not in hdrs
    assert url == "https://api.stripe.com/v1/charges"


def test_request_body_cap():
    with pytest.raises(eg.EgressError) as e:
        eg.build_request(_conn(), method="POST", path="/x", query=None, headers=None, body="x" * (300 * 1024), secret="k")
    assert e.value.code == "request_too_large"


def test_credential_reflection_redaction():
    sec, fp = "sk_live_SECRET", "deadbeef"
    b64 = base64.b64encode(sec.encode()).decode()
    body = f"invalid key {sec} enc {b64} fp {fp}"
    red = eg._redact(body, sec, fp)
    assert sec not in red and b64 not in red and fp not in red


def test_seal_unseal_roundtrip_and_tamper_fails_closed(monkeypatch):
    from plugins.takyon import safebox as sb
    monkeypatch.setattr(sb, "read_env_backed_value", lambda name: "unit-test-seal-key")
    ct, nonce, fp = eg.seal_secret("sk_live_TOPSECRET")
    assert eg._unseal_secret(ct, nonce) == "sk_live_TOPSECRET"
    assert fp == hashlib.sha256(b"sk_live_TOPSECRET").hexdigest()
    with pytest.raises(eg.EgressError) as e:
        eg._unseal_secret(ct[:-1] + bytes([ct[-1] ^ 1]), nonce)
    assert e.value.code == "connection_unseal_failed"


def test_seal_unconfigured_fails_closed(monkeypatch):
    from plugins.takyon import safebox as sb
    monkeypatch.setattr(sb, "read_env_backed_value", lambda name: "")
    with pytest.raises(eg.EgressError) as e:
        eg.seal_secret("x")
    assert e.value.code == "egress_seal_unconfigured"
