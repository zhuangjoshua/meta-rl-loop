"""Store-compliance gate (plugins/takyon/store_compliance.py) — stub-driven + real-binary tests.

Pins: lane thresholds (internal = zero CRITICALs; production = zero CRITICALs AND zero HIGHs —
deliberately OUR encoding, since greenlight's `passed` ignores HIGH and its --exit-code trips on
both); fail-closed on missing binary / unpinned platform / checksum mismatch / non-JSON output /
incomplete scans / timeout; receipt shape. The real-binary test runs the PINNED greenlight against
a minimal Expo-shaped project when the pinned binary is installed (skips cleanly otherwise)."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from plugins.takyon import store_compliance as sc


def _stub_binary(tmp_path: Path, payload: dict | str, *, exit_code: int = 0) -> Path:
    """A fake greenlight: prints the canned payload on any invocation."""
    out = payload if isinstance(payload, str) else json.dumps(payload)
    script = tmp_path / "greenlight-stub"
    script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{out}\nEOF\nexit {exit_code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def _summary(critical=0, high=0, warns=0, infos=0):
    total = critical + high + warns + infos
    return {"total": total, "critical": critical, "high": high, "warns": warns,
            "infos": infos, "passed": critical == 0}


def test_internal_lane_passes_with_highs_production_refuses(tmp_path):
    payload = {"summary": _summary(critical=0, high=2), "findings": [
        {"severity": "HIGH", "title": "missing ATT"}, {"severity": "HIGH", "title": "no restore"},
    ]}
    stub = _stub_binary(tmp_path, payload)
    proj = _project(tmp_path)
    internal = sc.run_preflight_gate(proj, lane=sc.LANE_INTERNAL, bin_path=stub)
    assert internal["passed"] is True
    production = sc.run_preflight_gate(proj, lane=sc.LANE_PRODUCTION, bin_path=stub)
    assert production["passed"] is False
    assert "missing ATT" in production["detail"]


def test_critical_fails_both_lanes(tmp_path):
    payload = {"summary": _summary(critical=1), "findings": [
        {"severity": "CRITICAL", "title": "hardcoded secret"},
    ]}
    stub = _stub_binary(tmp_path, payload)
    proj = _project(tmp_path)
    for lane in sc.LANES:
        r = sc.run_preflight_gate(proj, lane=lane, bin_path=stub)
        assert r["passed"] is False


def test_clean_scan_passes_production(tmp_path):
    stub = _stub_binary(tmp_path, {"summary": _summary(), "findings": []})
    r = sc.run_preflight_gate(_project(tmp_path), lane=sc.LANE_PRODUCTION, bin_path=stub)
    assert r["passed"] is True
    assert r["gate"] == "greenlight_preflight"
    assert r["greenlight_version"] == sc.GREENLIGHT_VERSION


def test_incomplete_scan_fails_closed(tmp_path):
    payload = {"summary": _summary(), "findings": [], "incomplete": True}
    stub = _stub_binary(tmp_path, payload)
    r = sc.run_preflight_gate(_project(tmp_path), bin_path=stub)
    assert r["passed"] is False
    assert "INCOMPLETE" in r["detail"]


def test_non_json_output_fails_closed(tmp_path):
    stub = _stub_binary(tmp_path, "GREENLIT! everything fine", exit_code=0)
    r = sc.run_preflight_gate(_project(tmp_path), bin_path=stub)
    assert r["passed"] is False
    assert ("non-JSON" in r["detail"]) or ("no report" in r["detail"])


def test_unknown_lane_and_missing_dir_raise(tmp_path):
    stub = _stub_binary(tmp_path, {"summary": _summary()})
    with pytest.raises(sc.StoreComplianceError):
        sc.run_preflight_gate(_project(tmp_path), lane="yolo", bin_path=stub)
    with pytest.raises(sc.StoreComplianceError):
        sc.run_preflight_gate(tmp_path / "nope", bin_path=stub)


def test_resolve_missing_binary_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setenv(sc.GREENLIGHT_BIN_ENV, str(tmp_path / "absent"))
    with pytest.raises(sc.GreenlightUnconfigured) as exc:
        sc.resolve_greenlight_bin()
    assert "greenlight_unconfigured" in str(exc.value)


def test_resolve_checksum_mismatch_untrusted(tmp_path, monkeypatch):
    fake = tmp_path / "greenlight"
    fake.write_text("#!/bin/sh\necho hi\n")
    fake.chmod(0o755)
    monkeypatch.setenv(sc.GREENLIGHT_BIN_ENV, str(fake))
    monkeypatch.setitem(sc.GREENLIGHT_SHA256, sc._platform_key(), "0" * 64)
    with pytest.raises(sc.GreenlightUntrusted) as exc:
        sc.resolve_greenlight_bin()
    assert "greenlight_untrusted" in str(exc.value)


def test_resolve_unpinned_platform_untrusted(tmp_path, monkeypatch):
    fake = tmp_path / "greenlight"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv(sc.GREENLIGHT_BIN_ENV, str(fake))
    monkeypatch.setattr(sc, "GREENLIGHT_SHA256", {})
    with pytest.raises(sc.GreenlightUntrusted):
        sc.resolve_greenlight_bin()


def test_resolve_accepts_matching_pin(tmp_path, monkeypatch):
    fake = tmp_path / "greenlight"
    fake.write_text("#!/bin/sh\necho ok\n")
    fake.chmod(0o755)
    digest = hashlib.sha256(fake.read_bytes()).hexdigest()
    monkeypatch.setenv(sc.GREENLIGHT_BIN_ENV, str(fake))
    monkeypatch.setitem(sc.GREENLIGHT_SHA256, sc._platform_key(), digest)
    assert sc.resolve_greenlight_bin() == fake


# ── real pinned binary (skips when not provisioned) ────────────────────────────────────


def _real_binary() -> Path | None:
    try:
        return sc.resolve_greenlight_bin(env=dict(os.environ))
    except sc.StoreComplianceError:
        return None


@pytest.mark.skipif(_real_binary() is None, reason="pinned greenlight binary not provisioned")
def test_real_binary_flags_expo_project_with_secret(tmp_path):
    proj = _project(tmp_path)
    (proj / "app.json").write_text(json.dumps({
        "expo": {"name": "canary", "version": "1.0.0",
                  "ios": {"bundleIdentifier": "com.example.canary"}, "icon": "./icon.png"}
    }))
    # A hardcoded Stripe live secret — greenlight's hardcoded-secrets rule is CRITICAL.
    (proj / "pay.ts").write_text('const k = "sk_live_' + "a1b2c3d4e5f6a1b2c3d4e5f6" + '";\n')
    r = sc.run_preflight_gate(proj, lane=sc.LANE_INTERNAL)
    assert r["passed"] is False
    assert int(r["summary"].get("critical") or 0) >= 1


@pytest.mark.skipif(_real_binary() is None, reason="pinned greenlight binary not provisioned")
def test_real_binary_clean_scan_produces_summary(tmp_path):
    proj = _project(tmp_path)
    (proj / "app.json").write_text(json.dumps({
        "expo": {"name": "canary-clean", "version": "1.0.0", "description": "a real description",
                  "ios": {"bundleIdentifier": "com.example.clean"}, "icon": "./icon.png"}
    }))
    (proj / "index.ts").write_text("export const hello = () => 'world';\n")
    r = sc.run_preflight_gate(proj, lane=sc.LANE_INTERNAL)
    # Not asserting GREENLIT (metadata warns vary by version) — asserting the CONTRACT: a parsed
    # summary with integer counts and threshold logic applied.
    assert isinstance(r["summary"].get("critical"), int)
    assert r["passed"] == (r["summary"]["critical"] == 0)
