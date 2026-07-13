from __future__ import annotations

from pathlib import Path

import pytest

from plugins.takyon import core


def _skill_receipt(**overrides: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "required": True,
        "installed": True,
        "discovered": True,
        "included": True,
        "included_source": "userSettings",
        "native_use": True,
        "native_use_events": 1,
        "prompt_body_absent": True,
        "prompt_distinctive_markers_absent": True,
        "installed_sha256": core._NATIVE_TASTE_SKILL_SHA256,
        "actual_model": "deepseek-v4-pro",
        "duration_ms": 3210,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    receipt.update(overrides)
    return receipt


@pytest.mark.parametrize("initial_pass", [True, False])
def test_native_taste_receipt_is_the_only_design_publication_contract(
    tmp_path: Path,
    initial_pass: bool,
):
    receipt = core._taste_worker_advisory_receipt(
        sdk_result={"skill_receipt": _skill_receipt()},
        baseline_snapshot=None,
        initial_pass=initial_pass,
    )

    assert receipt["passed"] is True
    assert receipt["advisory"] is False
    assert receipt["advisory_detail"] == ""
    assert receipt["skill_sha256"] == core._NATIVE_TASTE_SKILL_SHA256
    assert not (tmp_path / "DESIGN.md").exists()
    assert not (tmp_path / core.DESIGN_SNAPSHOT_RELATIVE_PATH).exists()


@pytest.mark.parametrize(
    ("sdk_result", "expected"),
    [
        ({}, "native Taste skill receipt is missing"),
        (
            {"skill_receipt": _skill_receipt(native_use=False, native_use_events=0)},
            "native Taste SDK receipt did not confirm",
        ),
        (
            {"skill_receipt": _skill_receipt(native_use_events="not-a-number")},
            "native Taste SDK receipt did not confirm",
        ),
        (
            {"skill_receipt": _skill_receipt(included_source="projectSettings")},
            "native Taste SDK receipt did not confirm",
        ),
        (
            {
                "skill_receipt": _skill_receipt(
                    prompt_distinctive_markers_absent=False,
                )
            },
            "native Taste SDK receipt did not confirm",
        ),
    ],
)
def test_missing_or_invalid_native_taste_receipt_is_advisory(
    tmp_path: Path,
    sdk_result: dict[str, object],
    expected: str,
):
    receipt = core._taste_worker_advisory_receipt(
        sdk_result=sdk_result,
        baseline_snapshot=None,
        initial_pass=True,
    )

    assert receipt["passed"] is False
    assert receipt["advisory"] is True
    assert expected in receipt["advisory_detail"]
