"""Custom-conversion ensure: create/recover -> READ-BACK VERIFY (the trust step).

The purchase-attribution boundary is only as trustworthy as this flow: an id returned by
create (or recovered from a duplicate) proves existence, not identity. These tests pin the
contract that only a conversion whose read-back matches id + pixel + event type + EXACT rule
is ever certified — and that duplicate recovery is a deterministic Meta LIST + exact-name
match, never guessed out of error text.
"""

from __future__ import annotations

import json

import pytest

from plugins.takyon import meta_graph


RULE = json.dumps({"url": {"i_contains": "clipbook.coscale.app"}})


def _fake_graph(script):
    """script: list of (method, path_predicate, response_or_exception)."""
    calls = []

    def fake(method, path, params=None, *, token=None, version="v21.0", **kw):
        calls.append({"method": method, "path": path, "params": dict(params or {})})
        for m, pred, resp in script:
            if m == method and pred(path):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected graph call {method} {path}")

    return fake, calls


def test_create_then_readback_verify_success(monkeypatch):
    fake, calls = _fake_graph([
        ("POST", lambda p: p.endswith("/customconversions"), {"id": "cc-9"}),
        ("GET", lambda p: p == "cc-9", {
            "id": "cc-9", "name": "clipbook-purchase",
            "custom_event_type": "PURCHASE", "rule": RULE,
            "pixel": {"id": "PIX-1"},
        }),
    ])
    monkeypatch.setattr(meta_graph, "_graph", fake)
    out = meta_graph.ensure_custom_conversion(
        "tok", "act_1", name="clipbook-purchase", rule=RULE,
        custom_event_type="PURCHASE", event_source_id="PIX-1")
    assert out["id"] == "cc-9" and out["verified"] is True and out["existed"] is False
    assert out["custom_event_type"] == "PURCHASE" and out["pixel_id"] == "PIX-1"
    # create carried the pixel anchor
    assert calls[0]["params"]["event_source_id"] == "PIX-1"


def test_duplicate_recovers_by_exact_name_list_not_error_text(monkeypatch):
    dup = meta_graph.MetaGraphError("Meta Graph POST failed (code 2650): name already exists id 99999")
    fake, calls = _fake_graph([
        ("POST", lambda p: p.endswith("/customconversions"), dup),
        ("GET", lambda p: p.endswith("/customconversions"), {
            "data": [
                {"id": "cc-other", "name": "someone-else"},
                {"id": "cc-42", "name": "clipbook-purchase"},
            ],
        }),
        ("GET", lambda p: p == "cc-42", {
            "id": "cc-42", "name": "clipbook-purchase",
            "custom_event_type": "PURCHASE", "rule": RULE,
            "pixel": {"id": "PIX-1"},
        }),
    ])
    monkeypatch.setattr(meta_graph, "_graph", fake)
    out = meta_graph.ensure_custom_conversion(
        "tok", "act_1", name="clipbook-purchase", rule=RULE,
        custom_event_type="PURCHASE", event_source_id="PIX-1")
    # Recovered id came from the LIST (cc-42), NOT from the digits in the error text (99999).
    assert out["id"] == "cc-42" and out["existed"] is True


def test_duplicate_with_no_name_match_reraises(monkeypatch):
    dup = meta_graph.MetaGraphError("already exists 12345")
    fake, _ = _fake_graph([
        ("POST", lambda p: p.endswith("/customconversions"), dup),
        ("GET", lambda p: p.endswith("/customconversions"), {"data": []}),
    ])
    monkeypatch.setattr(meta_graph, "_graph", fake)
    with pytest.raises(meta_graph.MetaGraphError):
        meta_graph.ensure_custom_conversion(
            "tok", "act_1", name="clipbook-purchase", rule=RULE,
            custom_event_type="PURCHASE", event_source_id="PIX-1")


@pytest.mark.parametrize("readback, problem", [
    ({"id": "cc-9", "custom_event_type": "LEAD", "rule": RULE, "pixel": {"id": "PIX-1"}},
     "custom_event_type"),
    ({"id": "cc-9", "custom_event_type": "PURCHASE", "rule": RULE, "pixel": {"id": "PIX-OTHER"}},
     "pixel"),
    ({"id": "cc-9", "custom_event_type": "PURCHASE",
      "rule": json.dumps({"url": {"i_contains": "othersaas.coscale.app"}}),
      "pixel": {"id": "PIX-1"}},
     "rule"),
])
def test_readback_mismatch_refuses_certification(monkeypatch, readback, problem):
    fake, _ = _fake_graph([
        ("POST", lambda p: p.endswith("/customconversions"), {"id": "cc-9"}),
        ("GET", lambda p: p == "cc-9", readback),
    ])
    monkeypatch.setattr(meta_graph, "_graph", fake)
    with pytest.raises(meta_graph.MetaGraphError) as exc:
        meta_graph.ensure_custom_conversion(
            "tok", "act_1", name="clipbook-purchase", rule=RULE,
            custom_event_type="PURCHASE", event_source_id="PIX-1")
    assert problem in str(exc.value)


def test_rule_comparison_is_canonical_not_textual(monkeypatch):
    # Meta may reserialize the rule (key order / whitespace); verification must compare
    # canonical JSON, not raw strings.
    reserialized = json.dumps(json.loads(RULE), indent=2)
    fake, _ = _fake_graph([
        ("POST", lambda p: p.endswith("/customconversions"), {"id": "cc-9"}),
        ("GET", lambda p: p == "cc-9", {
            "id": "cc-9", "custom_event_type": "PURCHASE",
            "rule": reserialized, "pixel": {"id": "PIX-1"},
        }),
    ])
    monkeypatch.setattr(meta_graph, "_graph", fake)
    out = meta_graph.ensure_custom_conversion(
        "tok", "act_1", name="clipbook-purchase", rule=RULE,
        custom_event_type="PURCHASE", event_source_id="PIX-1")
    assert out["verified"] is True


def test_missing_event_source_id_raises_before_any_call(monkeypatch):
    fake, calls = _fake_graph([])
    monkeypatch.setattr(meta_graph, "_graph", fake)
    with pytest.raises(ValueError):
        meta_graph.ensure_custom_conversion(
            "tok", "act_1", name="n", rule=RULE,
            custom_event_type="PURCHASE", event_source_id="")
    assert calls == []  # fails closed at the edge, no network


def test_digit_guessing_helper_is_gone():
    assert not hasattr(meta_graph, "_existing_conversion_id_from_error")


def test_purchase_rule_requires_private_event_and_exact_app_host():
    rule = json.loads(meta_graph.purchase_custom_conversion_rule(
        "TakyonPurchase_secret", "clipbook.coscale.app"
    ))
    assert {"event": {"eq": "TakyonPurchase_secret"}} in rule["and"]
    assert {"url": {"i_contains": "clipbook.coscale.app/app"}} in rule["and"]


def test_capi_purchase_sends_one_server_event(monkeypatch):
    captured = {}

    def fake_graph(method, path, params, **kwargs):
        captured.update(method=method, path=path, params=params, kwargs=kwargs)
        return {"events_received": 1}

    monkeypatch.setattr(meta_graph, "_graph", fake_graph)
    out = meta_graph.send_purchase_conversion_event(
        "capi-token", "123456",
        event_name="TakyonPurchase_secret",
        event_time=1_700_000_000,
        event_id="takyon-stripe:cs_1:evt_1",
        event_source_url="https://clipbook.coscale.app/app?checkout=success",
        user_data={"em": ["abc"]}, value=19.0, currency="usd",
    )
    payload = json.loads(captured["params"]["data"])[0]
    assert captured["method"] == "POST" and captured["path"] == "123456/events"
    assert payload["event_name"] == "TakyonPurchase_secret"
    assert payload["event_id"] == "takyon-stripe:cs_1:evt_1"
    assert payload["custom_data"] == {"value": 19.0, "currency": "USD"}
    assert out["events_received"] == 1
