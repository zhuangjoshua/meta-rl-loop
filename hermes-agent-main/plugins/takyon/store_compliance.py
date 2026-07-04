"""Store-compliance gate — the greenlight preflight rail (readmodular §3, App Store rail).

Wraps the pinned RevylAI **greenlight** scanner (MIT, offline, <1s) as a fail-closed,
receipt-producing gate for store-bound mobile builds. This is a `compliance_gates` entry in the
archetype preset (``greenlight_preflight`` on ``mobile_app``) — deterministic safety-rail code,
the allowed hardcode class.

Threshold contract (ours, not greenlight's): greenlight's own ``summary.passed`` is "zero
CRITICALs" and its ``--exit-code`` trips on CRITICAL **or** HIGH — so we parse the JSON and
encode lanes explicitly (verified against the greenlight source):

  * ``internal``   lane (sim/TestFlight-internal): ``critical == 0``.
  * ``production`` lane (App Store submission):    ``critical == 0 and high == 0``.

Fail-closed posture, standing rules:
  * Binary missing/unreadable → ``greenlight_unconfigured`` (never skip-and-pass).
  * Binary present but sha256 differs from the PIN → refused (``greenlight_untrusted``) — a gate
    that executes an unpinned scanner is not a gate.
  * Scanner crash / bad JSON / ``incomplete`` scans → gate FAILS (never "scanner broke, ship it").
  * greenlight is free + offline: no money gate needed (nothing paid); the Revyl ``verify`` tier
    is a paid third-party product and is deliberately NOT wired (no ungated paid capability).

Subuser security: zero subuser surface — this runs on the operator/worker plane against the
mobile source workspace; no secrets, no network (preflight is offline), no new routes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

# ── the pin (version + sha256 per platform) ───────────────────────────────────────────────
# Built from the pinned upstream source (RevylAI/greenlight v0.1.0, MIT). Rebuilding/upgrading
# means updating BOTH the version and the shas here — the gate refuses any other binary.
GREENLIGHT_VERSION = "v0.1.0"
# sha256 of the trusted binaries, keyed by (goos, goarch). Filled at provisioning time by the
# tracked build (scripts below in the deploy notes); an empty map means "not provisioned yet" and
# every scan refuses greenlight_unconfigured — fail closed, never fail open.
GREENLIGHT_SHA256: dict[str, str] = {
    # Built 2026-07-04 from the v0.1.0 tag (commit d1c53a6) with:
    #   GOFLAGS=-trimpath CGO_ENABLED=0 [GOOS=linux GOARCH=amd64] go build -ldflags="-s -w" ./cmd/greenlight
    "darwin-arm64": "00f8866783822949bdd830d04df2264deb8a9d6e893e0a6944d8b2e5da07253d",
    "linux-amd64": "1160ef88159c0150a34d5be6e660468d96d9f445b0d23e5bf005f2f9647c3bfe",
}
# Config seam: explicit path wins; else PATH lookup. Checksum is enforced either way.
GREENLIGHT_BIN_ENV = "TAKYON_GREENLIGHT_BIN"

LANE_INTERNAL = "internal"
LANE_PRODUCTION = "production"
LANES = (LANE_INTERNAL, LANE_PRODUCTION)


class StoreComplianceError(Exception):
    """Base for compliance-gate errors."""


class GreenlightUnconfigured(StoreComplianceError):
    """The pinned scanner binary is absent — the gate cannot run and therefore refuses. The
    message carries the exact ``greenlight_unconfigured`` token (CEO discovery surface)."""


class GreenlightUntrusted(StoreComplianceError):
    """A binary was found but its sha256 does not match the pin (or no pin is recorded for this
    platform). Refused — executing an unpinned scanner is not a gate."""


def _platform_key() -> str:
    import platform

    goos = {"darwin": "darwin", "linux": "linux"}.get(platform.system().lower(), platform.system().lower())
    machine = platform.machine().lower()
    goarch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(machine, machine)
    return f"{goos}-{goarch}"


def resolve_greenlight_bin(*, env: dict[str, str] | None = None) -> Path:
    """Locate AND verify the pinned greenlight binary. Explicit ``TAKYON_GREENLIGHT_BIN`` wins,
    else PATH. Missing → ``GreenlightUnconfigured``; checksum mismatch or unpinned platform →
    ``GreenlightUntrusted``. Never returns an unverified path."""
    environ = env if env is not None else os.environ
    explicit = str(environ.get(GREENLIGHT_BIN_ENV) or "").strip()
    candidate = Path(explicit) if explicit else None
    if candidate is None:
        found = shutil.which("greenlight")
        candidate = Path(found) if found else None
    if candidate is None or not candidate.is_file():
        raise GreenlightUnconfigured(
            "greenlight_unconfigured: the pinned greenlight scanner binary is not installed "
            f"(set {GREENLIGHT_BIN_ENV} or put 'greenlight' on PATH). Store-bound builds are "
            "refused until the compliance gate can run."
        )
    key = _platform_key()
    pinned = GREENLIGHT_SHA256.get(key, "")
    if not pinned:
        raise GreenlightUntrusted(
            f"greenlight_untrusted: no pinned sha256 recorded for platform {key} "
            f"(greenlight {GREENLIGHT_VERSION}). Provision the pinned build first."
        )
    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if actual != pinned:
        raise GreenlightUntrusted(
            f"greenlight_untrusted: binary at {candidate} sha256 {actual[:12]}… does not match "
            f"the pinned {GREENLIGHT_VERSION} build {pinned[:12]}… — refusing to gate with an "
            "unpinned scanner."
        )
    return candidate


def _thresholds_pass(lane: str, summary: dict[str, Any]) -> bool:
    critical = int(summary.get("critical") or 0)
    high = int(summary.get("high") or 0)
    if lane == LANE_PRODUCTION:
        return critical == 0 and high == 0
    return critical == 0


def run_preflight_gate(
    project_dir: str | Path,
    *,
    lane: str = LANE_INTERNAL,
    source_digest: str = "",
    timeout_seconds: float = 120.0,
    bin_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run ``greenlight preflight <dir> --format json`` and gate on OUR lane thresholds.

    Returns a receipt dict (never raises for scan findings — only for misconfiguration):

        {"gate": "greenlight_preflight", "lane", "passed": bool, "summary": {...},
         "finding_count": int, "suppression_note", "greenlight_version", "bin_sha256",
         "source_digest", "checked_at", "detail"}

    ``passed=False`` receipts carry the top findings' titles in ``detail`` so the worker's fix
    loop (the skill) can act without re-parsing. A crashed scanner, non-JSON output, or an
    ``incomplete`` scan is ``passed=False`` — the gate never fails open."""
    if lane not in LANES:
        raise StoreComplianceError(f"unknown compliance lane {lane!r}; must be one of {LANES}")
    project = Path(project_dir)
    if not project.is_dir():
        raise StoreComplianceError(f"project_dir {project} is not a directory")
    binary = bin_path or resolve_greenlight_bin(env=env)
    checked_at = int(time.time())

    def _receipt(passed: bool, *, summary: dict[str, Any] | None = None, detail: str = "",
                 finding_count: int = 0) -> dict[str, Any]:
        return {
            "gate": "greenlight_preflight",
            "lane": lane,
            "passed": bool(passed),
            "summary": dict(summary or {}),
            "finding_count": int(finding_count),
            "greenlight_version": GREENLIGHT_VERSION,
            "bin_sha256": GREENLIGHT_SHA256.get(_platform_key(), ""),
            "source_digest": str(source_digest or ""),
            "checked_at": checked_at,
            "detail": detail[:500],
        }

    # greenlight prints a human preamble on stdout even with --format json; --output writes the
    # PURE JSON report to a file, so the gate reads that (robust against banner changes).
    import tempfile

    report_path = Path(tempfile.mkstemp(prefix="greenlight-", suffix=".json")[1])
    try:
        try:
            proc = subprocess.run(
                [str(binary), "preflight", str(project), "--format", "json",
                 "--output", str(report_path)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(project),
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")},
            )
        except subprocess.TimeoutExpired:
            return _receipt(False, detail=f"greenlight timed out after {timeout_seconds}s — gate fails closed")
        except OSError as exc:
            return _receipt(False, detail=f"greenlight failed to execute: {exc} — gate fails closed")

        raw = ""
        try:
            raw = report_path.read_text().strip()
        except OSError:
            raw = ""
        if not raw:
            # Stub/test seam + defensive fallback: some builds may print JSON to stdout only.
            stdout = (proc.stdout or "").strip()
            start = stdout.find("{")
            raw = stdout[start:] if start >= 0 else ""
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return _receipt(
                False,
                detail=f"greenlight emitted non-JSON output (exit {proc.returncode}): "
                       f"{(proc.stderr or raw)[:200]} — gate fails closed",
            )
        if not raw:
            return _receipt(
                False,
                detail=f"greenlight produced no report (exit {proc.returncode}): "
                       f"{(proc.stderr or proc.stdout or '')[:200]} — gate fails closed",
            )
    finally:
        try:
            report_path.unlink()
        except OSError:
            pass
    if not isinstance(payload, dict):
        return _receipt(False, detail="greenlight JSON was not an object — gate fails closed")

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    if payload.get("incomplete"):
        return _receipt(
            False,
            summary=summary,
            finding_count=len(findings),
            detail="greenlight reported an INCOMPLETE scan (a sub-scanner crashed) — gate fails closed",
        )
    passed = _thresholds_pass(lane, summary)
    detail = ""
    if not passed:
        worst = [
            f"[{f.get('severity')}] {f.get('title')}"
            for f in findings
            if isinstance(f, dict) and str(f.get("severity")) in {"CRITICAL", "HIGH"}
        ][:8]
        detail = "; ".join(worst) or (
            f"thresholds not met for lane={lane}: critical={summary.get('critical')}, high={summary.get('high')}"
        )
    return _receipt(passed, summary=summary, finding_count=len(findings), detail=detail)
