from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.takyon import core
from plugins.takyon.taste_publication_gate import (
    AssetVisualInspection,
    RenderInspection,
    TasteDesignSnapshot,
)


def _snapshot() -> TasteDesignSnapshot:
    return TasteDesignSnapshot(
        version=1,
        design_sha256="1" * 64,
        landing_sha256="2" * 64,
        tokens_sha256="3" * 64,
        design_read_sha256="4" * 64,
        foundation_sha256="5" * 64,
        dials={
            "DESIGN_VARIANCE": 6,
            "MOTION_INTENSITY": 4,
            "VISUAL_DENSITY": 5,
        },
        tokens={"--ink": "#111111"},
        assets={
            "/generated/hero.png": "a" * 64,
            "/generated/detail.png": "b" * 64,
        },
    )


def _skill_receipt() -> dict[str, object]:
    return {
        "required": True,
        "installed": True,
        "discovered": True,
        "included": True,
        "included_source": "userSettings",
        "native_use": True,
        "native_use_events": 1,
        "prompt_body_absent": True,
        "installed_sha256": core._NATIVE_TASTE_SKILL_SHA256,
        "actual_model": "deepseek-v4-pro",
        "duration_ms": 3210,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


def _publication_evidence(workspace: Path) -> dict[str, object]:
    scratch = workspace / ".takyon-preflight"
    scratch.mkdir(parents=True)
    for name in ("landing-desktop.png", "landing-mobile.png"):
        (scratch / name).write_bytes(b"proof")
    official = {
        gate_id: {
            "passed": True,
            "evidence": f"evidence for {gate_id}",
            "source": "native Taste audit",
        }
        for gate_id in core._OFFICIAL_TASTE_PUBLICATION_GATE_IDS
    }
    return {
        "submitted": True,
        "passed": True,
        "official_gates": official,
        "preflight_evidence": {},
        "render_inspections": {
            "desktop": {
                "width": 1440,
                "height": 900,
                "screenshot_path": "/workspace/.takyon-preflight/landing-desktop.png",
                "screenshot_sha256": "c" * 64,
                "inspected": True,
                "probe": {"viewport_width": 1440, "viewport_height": 900},
            },
            "mobile": {
                "width": 390,
                "height": 844,
                "screenshot_path": "/workspace/.takyon-preflight/landing-mobile.png",
                "screenshot_sha256": "d" * 64,
                "inspected": True,
                "probe": {"viewport_width": 390, "viewport_height": 844},
            },
        },
        "asset_inspections": {
            "/generated/hero.png": {
                "public_path": "/generated/hero.png",
                "image_sha256": "a" * 64,
                "inspected": True,
                "inspected_width": 1600,
                "inspected_height": 900,
                "source": "/workspace/public/generated/hero.png",
            },
            "/generated/detail.png": {
                "public_path": "/generated/detail.png",
                "image_sha256": "b" * 64,
                "inspected": True,
                "inspected_width": 1200,
                "inspected_height": 900,
                "source": "/workspace/public/generated/detail.png",
            },
        },
    }


class _Bridge:
    def __init__(self, *, generated_this_run_count: int = 2):
        self.generated_this_run_count = generated_this_run_count

    def restore_generated_assets(self) -> int:
        return 2

    def authoritative_asset_digests(self) -> dict[str, str]:
        return dict(_snapshot().assets)


def test_taste_initial_classification_uses_valid_snapshot_not_design_md(tmp_path: Path):
    (tmp_path / "DESIGN.md").write_text("legacy design is not a validated handoff\n", encoding="utf-8")
    assert core._load_validated_taste_design_snapshot(tmp_path) is None

    snapshot_path = tmp_path / core.DESIGN_SNAPSHOT_RELATIVE_PATH
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(_snapshot().to_dict()), encoding="utf-8")
    assert core._load_validated_taste_design_snapshot(tmp_path) == _snapshot()

    snapshot_path.write_text("{}", encoding="utf-8")
    with pytest.raises(core.TakyonError, match="snapshot is invalid"):
        core._load_validated_taste_design_snapshot(tmp_path)


def test_core_validates_helper_evidence_before_writing_initial_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    evidence = _publication_evidence(tmp_path)
    current_snapshot = _snapshot()
    observed: dict[str, object] = {}

    def fake_gate(workspace_path, **kwargs):
        observed["workspace_path"] = workspace_path
        observed.update(kwargs)
        return SimpleNamespace(
            passed=True,
            blocker="",
            snapshot=current_snapshot,
            to_dict=lambda: {"passed": True, "snapshot": current_snapshot.to_dict()},
        )

    monkeypatch.setattr(core, "validate_taste_publication", fake_gate)
    receipt, blocker = core._validate_taste_worker_publication(
        workspace_path=tmp_path,
        sdk_result={
            "skill_receipt": _skill_receipt(),
            "taste_publication_evidence": evidence,
        },
        baseline_snapshot=None,
        site_image_bridge=_Bridge(),
        initial_pass=True,
    )

    assert blocker == ""
    assert receipt["passed"] is True
    assert receipt["generated_this_run_count"] == 2
    assert receipt["authoritative_asset_digests"] == current_snapshot.assets
    assert isinstance(observed["desktop"], RenderInspection)
    assert isinstance(observed["mobile"], RenderInspection)
    assert Path(observed["desktop"].screenshot_path) == (
        tmp_path / ".takyon-preflight" / "landing-desktop.png"
    )
    assert all(
        isinstance(value, AssetVisualInspection)
        for value in observed["asset_inspections"].values()
    )
    assert observed["baseline_snapshot"] is None
    written = json.loads((tmp_path / core.DESIGN_SNAPSHOT_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert written == current_snapshot.to_dict()


def test_core_rejects_asset_evidence_outside_bridge_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    evidence = _publication_evidence(tmp_path)
    evidence["asset_inspections"]["/generated/hero.png"]["image_sha256"] = "f" * 64
    monkeypatch.setattr(
        core,
        "validate_taste_publication",
        lambda *_args, **_kwargs: pytest.fail("authority mismatch must block before Python gate"),
    )

    receipt, blocker = core._validate_taste_worker_publication(
        workspace_path=tmp_path,
        sdk_result={
            "skill_receipt": _skill_receipt(),
            "taste_publication_evidence": evidence,
        },
        baseline_snapshot=None,
        site_image_bridge=_Bridge(),
        initial_pass=True,
    )

    assert receipt["passed"] is False
    assert "parent-authoritative creative bridge outputs" in blocker
    assert not (tmp_path / core.DESIGN_SNAPSHOT_RELATIVE_PATH).exists()


@pytest.mark.parametrize(
    ("sdk_result", "expected"),
    [
        ({}, "native Taste skill receipt is missing"),
        ({"skill_receipt": _skill_receipt()}, "rendered Taste publication evidence is missing"),
    ],
)
def test_every_product_site_pass_requires_native_and_rendered_receipts(
    tmp_path: Path,
    sdk_result: dict[str, object],
    expected: str,
):
    receipt, blocker = core._validate_taste_worker_publication(
        workspace_path=tmp_path,
        sdk_result=sdk_result,
        baseline_snapshot=None,
        site_image_bridge=_Bridge(),
        initial_pass=True,
    )

    assert receipt["passed"] is False
    assert expected in blocker
    assert not (tmp_path / core.DESIGN_SNAPSHOT_RELATIVE_PATH).exists()


def test_initial_pass_requires_two_new_bridge_generated_assets(tmp_path: Path):
    receipt, blocker = core._validate_taste_worker_publication(
        workspace_path=tmp_path,
        sdk_result={
            "skill_receipt": _skill_receipt(),
            "taste_publication_evidence": _publication_evidence(tmp_path),
        },
        baseline_snapshot=None,
        site_image_bridge=_Bridge(generated_this_run_count=1),
        initial_pass=True,
    )

    assert receipt["passed"] is False
    assert receipt["generated_this_run_count"] == 1
    assert "did not generate two authoritative assets" in blocker


def test_later_product_pass_uses_and_preserves_validated_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    baseline = _snapshot()
    snapshot_path = tmp_path / core.DESIGN_SNAPSHOT_RELATIVE_PATH
    snapshot_path.parent.mkdir(parents=True)
    original = json.dumps(baseline.to_dict(), indent=2, sort_keys=True) + "\n"
    snapshot_path.write_text(original, encoding="utf-8")
    evidence = _publication_evidence(tmp_path)
    observed: dict[str, object] = {}

    def fake_gate(_workspace_path, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(
            passed=True,
            blocker="",
            snapshot=baseline,
            to_dict=lambda: {"passed": True, "snapshot": baseline.to_dict()},
        )

    monkeypatch.setattr(core, "validate_taste_publication", fake_gate)
    receipt, blocker = core._validate_taste_worker_publication(
        workspace_path=tmp_path,
        sdk_result={
            "skill_receipt": _skill_receipt(),
            "taste_publication_evidence": evidence,
        },
        baseline_snapshot=core._load_validated_taste_design_snapshot(tmp_path),
        site_image_bridge=_Bridge(generated_this_run_count=0),
        initial_pass=False,
    )

    assert blocker == ""
    assert receipt["passed"] is True
    assert receipt["snapshot_written"] is False
    assert observed["baseline_snapshot"] == baseline
    assert snapshot_path.read_text(encoding="utf-8") == original
