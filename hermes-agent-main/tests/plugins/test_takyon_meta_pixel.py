"""
Focused tests for the Meta Pixel tools (takyon-meta-ads-v2).

Per skills/takyon HANDOFF: stdlib + pytest + unittest.mock only; no live network. Mock
the authority gateway; the autouse temp-TAKYON_HOME fixture isolates state.
"""

from unittest.mock import patch
import json


# --- ensure: blocks when the shared pixel isn't configured (no faking) -------
def test_ensure_blocks_without_shared_pixel_id():
    from plugins.takyon import core
    with patch.object(core, "_call_creative_runtime_gateway",
                      return_value=json.dumps({"status": "blocked", "reason": "meta_pixel_unconfigured"})):
        out = json.loads(core.handle_business_meta_pixel_ensure(
            {"business": "acme", "idempotency_key": "k1"}))
    assert out["status"] == "blocked" and out["reason"] == "meta_pixel_unconfigured"


# --- ensure: passes the per-business conversion path + event type through -----
def test_ensure_passes_conversion_rule():
    from plugins.takyon import core
    with patch.object(core, "_call_creative_runtime_gateway", return_value="{}") as gw:
        core.handle_business_meta_pixel_ensure(
            {"business": "acme", "conversion_path": "/thank-you",
             "custom_event_type": "PURCHASE", "idempotency_key": "k2"})
    payload = gw.call_args.args[1]
    assert payload["conversion_path"] == "/thank-you"
    assert payload["custom_event_type"] == "PURCHASE"


# --- ensure: defaults event type to LEAD --------------------------------------
def test_ensure_defaults_event_type_lead():
    from plugins.takyon import core
    with patch.object(core, "_call_creative_runtime_gateway", return_value="{}") as gw:
        core.handle_business_meta_pixel_ensure({"business": "acme", "idempotency_key": "k3"})
    assert gw.call_args.args[1]["custom_event_type"] == "LEAD"


# --- verify: ok only when BOTH proofs pass (both serving paths + dataset) ------
def test_verify_requires_both_paths_and_dataset():
    from plugins.takyon import core
    # snippet present on VPS but missing on R2 -> not ok
    res = json.dumps({"installed_vps": True, "installed_r2": False,
                      "dataset_ok": True, "custom_conversion_ok": True, "ok": False})
    with patch.object(core, "_call_creative_runtime_gateway", return_value=res):
        out = json.loads(core.handle_business_meta_pixel_verify(
            {"business": "acme", "idempotency_key": "k4"}))
    assert out["installed_r2"] is False and out["ok"] is False


def test_verify_ok_when_all_proofs_pass():
    from plugins.takyon import core
    res = json.dumps({"installed_vps": True, "installed_r2": True,
                      "dataset_ok": True, "custom_conversion_ok": True, "ok": True})
    with patch.object(core, "_call_creative_runtime_gateway", return_value=res):
        out = json.loads(core.handle_business_meta_pixel_verify(
            {"business": "acme", "idempotency_key": "k5"}))
    assert out["ok"] is True


# --- build injection: idempotent and truthfully fails without index.html ------
def test_meta_pixel_snippet_injection_is_idempotent(tmp_path):
    from plugins.takyon import core

    index = tmp_path / "index.html"
    index.write_text("<html><head><title>x</title></head><body></body></html>", encoding="utf-8")

    assert core._inject_meta_pixel_snippet(tmp_path, pixel_id="1340991031280635") is True
    first = index.read_text(encoding="utf-8")
    assert 'data-takyon-meta-pixel="1340991031280635"' in first

    assert core._inject_meta_pixel_snippet(tmp_path, pixel_id="1340991031280635") is True
    second = index.read_text(encoding="utf-8")
    assert second == first


def test_meta_pixel_snippet_injection_requires_index(tmp_path):
    from plugins.takyon import core

    assert core._inject_meta_pixel_snippet(tmp_path, pixel_id="1340991031280635") is False
