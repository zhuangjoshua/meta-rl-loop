"""Mobile one-shot lane (2026-07-08 god-mode enablement) — pure/rig-free pins.

Covers the four seams that turned `create --archetype app` into a real app pipeline:
  * archetype-aware bootstrap (step 5 + turn-cap bump, web prose byte-unchanged),
  * the host-independent builder lane (safebox-minted credentials resolver + builder_mode),
  * settle-at-trigger receipts carrying the expo.dev logs_url,
  * the operator creative-gate bypass flag parsing (client + authority halves).

The money-order invariants themselves live in test_takyon_store_build.py; the live proof is the
fresh-business E2E (create → bootstrap → signed preview-lane build).
"""

from __future__ import annotations

import pytest

from plugins.takyon import store_build, store_builder
from plugins.takyon.turn_runtime import (
    _MOBILE_BOOTSTRAP_EXTRA_TURNS,
    _bootstrap_turn_cap_for_goal,
    _business_bootstrap_instruction,
)


# ── archetype-aware bootstrap ─────────────────────────────────────────────────────────────


def test_bootstrap_turn_cap_bumps_only_for_mobile():
    web = _bootstrap_turn_cap_for_goal("build a saas")
    assert _bootstrap_turn_cap_for_goal("build a saas", archetype="mobile_app") == web + _MOBILE_BOOTSTRAP_EXTRA_TURNS
    assert _bootstrap_turn_cap_for_goal("build a saas", archetype="web_saas") == web
    assert _bootstrap_turn_cap_for_goal("build a saas", archetype="") == web


def test_bootstrap_instruction_web_prose_unchanged_without_archetype():
    base = _business_bootstrap_instruction("acme", "goal", "live", business_name="Acme")
    explicit_web = _business_bootstrap_instruction(
        "acme", "goal", "live", business_name="Acme", archetype="web_saas"
    )
    assert base == explicit_web
    assert "### 5. iOS app" not in base
    assert "takyon-mobile-app" not in base


def test_bootstrap_instruction_mobile_gains_step_3_without_x():
    mobile = _business_bootstrap_instruction(
        "acme", "goal", "live", business_name="Acme", archetype="mobile_app"
    )
    assert "### 3. iOS app build + first store-signed build" in mobile
    assert "takyon-mobile-app at step 3" in mobile
    assert "business_publish_mobile_release with lane preview" in mobile
    assert "FRESH idempotency_key" in mobile
    assert "### 4. X post" not in mobile


# ── host-independent builder lane ─────────────────────────────────────────────────────────


def _full_mint_payload() -> dict:
    return {
        "business": "acme",
        "bundle_identifier": "com.coscale.acme",
        "team_id": "TEAM123456",
        "expo_owner": "coscale",
        "expo_token": "tok_" + "x" * 20,
        "dist_cert_id": "CERTID",
        "dist_p12_b64": "cDEyYnl0ZXM=",
        "dist_p12_password": "pw",
        "profile_b64": "cHJvZmlsZQ==",
        "minted_at": 1,
    }


def test_safebox_resolver_returns_preminted_creds(monkeypatch):
    from plugins.takyon import safebox as sb

    monkeypatch.setattr(sb, "store_eas_build_credentials", lambda business, capabilities=None: _full_mint_payload())
    creds = store_builder.resolve_safebox_store_credentials("acme", capabilities=["ASSOCIATED_DOMAINS"])
    # ASC key fields stay EMPTY — the .p8 never reaches this host.
    assert creds.key_id == "" and creds.issuer_id == "" and creds.private_key_pem == ""
    assert creds.preminted_profile_b64 == "cHJvZmlsZQ=="
    assert creds.dist_p12_b64 == "cDEyYnl0ZXM=" and creds.dist_p12_path == ""
    assert creds.expo_token.startswith("tok_")
    # repr never leaks secret material.
    assert "tok_" not in repr(creds) and "pw" not in repr(creds)


@pytest.mark.parametrize("missing", ["expo_token", "dist_p12_b64", "profile_b64", "dist_p12_password"])
def test_safebox_resolver_fails_closed_on_incomplete_bundle(monkeypatch, missing):
    from plugins.takyon import safebox as sb

    payload = _full_mint_payload()
    payload[missing] = ""
    monkeypatch.setattr(sb, "store_eas_build_credentials", lambda business, capabilities=None: payload)
    with pytest.raises(store_builder.StoreBuilderUnconfigured):
        store_builder.resolve_safebox_store_credentials("acme")


def test_safebox_resolver_fails_closed_on_transport_error(monkeypatch):
    from plugins.takyon import safebox as sb

    def _boom(business, capabilities=None):
        raise RuntimeError("safebox unreachable")

    monkeypatch.setattr(sb, "store_eas_build_credentials", _boom)
    with pytest.raises(store_builder.StoreBuilderUnconfigured):
        store_builder.resolve_safebox_store_credentials("acme")


def test_builder_mode_local_wins(monkeypatch):
    monkeypatch.setattr(store_builder, "is_configured", lambda secrets_dir=None: True)
    assert store_builder.builder_mode() == "local"


def test_builder_mode_safebox_when_toolchain_and_remote(monkeypatch):
    from plugins.takyon import safebox as sb

    monkeypatch.setattr(store_builder, "is_configured", lambda secrets_dir=None: False)
    monkeypatch.setattr(store_builder.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(sb, "_use_remote_authority", lambda: True)
    assert store_builder.builder_mode() == "safebox"


def test_builder_mode_empty_without_toolchain(monkeypatch):
    monkeypatch.setattr(store_builder, "is_configured", lambda secrets_dir=None: False)
    monkeypatch.setattr(store_builder.shutil, "which", lambda name: None)
    assert store_builder.builder_mode() == ""


def test_builder_mode_empty_without_remote_safebox(monkeypatch):
    from plugins.takyon import safebox as sb

    monkeypatch.setattr(store_builder, "is_configured", lambda secrets_dir=None: False)
    monkeypatch.setattr(store_builder.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(sb, "_use_remote_authority", lambda: False)
    assert store_builder.builder_mode() == ""


# ── settle metadata carries the logs receipt ──────────────────────────────────────────────


def test_run_build_settle_metadata_carries_logs_url():
    settled: dict = {}

    def reserve(business, credits, key):
        return {"key": key}

    def settle(key, actual, meta):
        settled.update(meta)

    def release(key, meta):  # pragma: no cover - not hit on success
        raise AssertionError("release must not run on a successful trigger")

    result = store_build.run_build(
        business_slug="acme",
        lane="preview",
        credits=4,
        reservation_key="rk-1",
        reserve=reserve,
        settle=settle,
        release=release,
        invoke_eas=lambda: store_build.BuildResult(
            build_id="b-1", lane="preview", logs_url="https://expo.dev/accounts/coscale/projects/acme/builds/b-1"
        ),
    )
    assert result.logs_url.endswith("/builds/b-1")
    assert settled["build_id"] == "b-1"
    assert settled["logs_url"].endswith("/builds/b-1")


def test_run_build_without_logs_url_omits_the_key():
    settled: dict = {}
    store_build.run_build(
        business_slug="acme",
        lane="preview",
        credits=4,
        reservation_key="rk-2",
        reserve=lambda b, c, k: None,
        settle=lambda k, a, m: settled.update(m),
        release=lambda k, m: None,
        invoke_eas=lambda: store_build.BuildResult(build_id="b-2", lane="preview"),
    )
    assert "logs_url" not in settled


# ── operator creative-gate bypass flag (both halves parse identically) ────────────────────


# ON BY DEFAULT (2026-07-09): unset/empty/1/true/on ⇒ True; only explicit 0/false/no/off ⇒ False.
@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("YES", True), ("on", True), ("", True),
     ("0", False), ("false", False), ("no", False), ("OFF", False)],
)
def test_operator_creative_gate_flag_parsing(monkeypatch, raw, expected):
    from plugins.takyon import core, safebox_app

    if raw:
        monkeypatch.setenv("TAKYON_OPERATOR_CREATIVE_GATE_DISABLED", raw)
    else:
        monkeypatch.delenv("TAKYON_OPERATOR_CREATIVE_GATE_DISABLED", raising=False)
    assert core._operator_creative_gate_disabled() is expected
    assert safebox_app._operator_creative_gate_disabled() is expected


def test_operator_creative_gate_default_on_when_unset(monkeypatch):
    from plugins.takyon import core, safebox_app

    monkeypatch.delenv("TAKYON_OPERATOR_CREATIVE_GATE_DISABLED", raising=False)
    assert core._operator_creative_gate_disabled() is True
    assert safebox_app._operator_creative_gate_disabled() is True


# ── regression pin: the mobile_release audience must ride the safebox creative gate ───────
# (c87547a0 was clobbered once by a stale-base core.py push; a missing entry silently
# downgrades the reserve to the local branch, which refuses on remote planes.)


def test_mobile_release_audience_registered_on_both_halves():
    from plugins.takyon import core, safebox_app

    assert core._creative_credit_action_audience("mobile_release") == "creative.mobile_release"
    assert safebox_app._CREATIVE_AUDIENCE_CREDIT_ACTION.get("creative.mobile_release") == "mobile_release"


# ── X launch-post dedupe across re-enqueued bootstraps ────────────────────────────────────


def test_x_launch_post_is_refused_during_bootstrap_before_receipt_lookup(monkeypatch, tmp_path):
    import json as _json
    from plugins.takyon import core

    commits = []

    class _Store:
        def commit(self, **kwargs):
            commits.append(kwargs)
            return {"success": True}

    receipt_path = tmp_path / "x-receipt.json"
    receipt_path.write_text('{"sent":true}\n', encoding="utf-8")
    monkeypatch.setattr(core, "_store", _Store)
    monkeypatch.setattr(core, "_resolved_business_slug", lambda args, required=False: "acme")
    monkeypatch.setattr(core, "_active_operator_task_kind", lambda: "ceo_bootstrap")
    monkeypatch.setattr(
        core,
        "_active_operator_task_receipt_context",
        lambda: {"task_kind": "ceo_bootstrap", "run_id": "bootstrap-job", "attempt": 2},
    )
    monkeypatch.setattr(
        core, "_x_outreach_receipt_candidates",
        lambda store, business: [{
            "post_id": "111",
            "post_url": "https://x.com/i/status/111",
            "receipt_rel": "metrics/receipts/outreach/a.json",
            "receipt_abs": str(receipt_path),
            "receipt": {
                "sent": True,
                "external_side_effects": "sent",
                "operator_task": {
                    "task_kind": "ceo_bootstrap",
                    "run_id": "bootstrap-job",
                    "attempt": 1,
                }
            },
        }],
    )
    out = _json.loads(core._handle_live_business_x_publish_outreach({"business": "acme", "channel": "x", "provider": "x", "body": "hi"}))
    assert out["success"] is False
    assert out["status"] == "not_allowed_in_bootstrap"
    assert out["external_side_effects"] == "none"
    assert commits == []


@pytest.mark.parametrize(
    ("receipt", "owner_text"),
    [
        (
            {
                "operator_task": {
                    "task_kind": "ceo_bootstrap",
                    "run_id": "old-job",
                    "attempt": 1,
                }
            },
            "old-job",
        ),
        ({}, "unscoped legacy receipt"),
    ],
)
def test_x_launch_post_refuses_stale_business_wide_receipt(
    monkeypatch,
    receipt,
    owner_text,
):
    import json as _json
    from plugins.takyon import core

    commits = []

    class _Store:
        def commit(self, **kwargs):
            commits.append(kwargs)
            return {"success": True}

    monkeypatch.setattr(core, "_store", _Store)
    monkeypatch.setattr(core, "_resolved_business_slug", lambda args, required=False: "acme")
    monkeypatch.setattr(core, "_active_operator_task_kind", lambda: "ceo_bootstrap")
    monkeypatch.setattr(
        core,
        "_active_operator_task_receipt_context",
        lambda: {"task_kind": "ceo_bootstrap", "run_id": "new-job", "attempt": 1},
    )
    monkeypatch.setattr(
        core,
        "_x_outreach_receipt_candidates",
        lambda store, business: [{
            "post_id": "old-111",
            "post_url": "https://x.com/i/status/old-111",
            "receipt_rel": "metrics/receipts/outreach/old.json",
            "receipt": receipt,
        }],
    )

    out = _json.loads(
        core._handle_live_business_x_publish_outreach(
            {"business": "acme", "channel": "x", "provider": "x", "body": "hi"}
        )
    )

    assert out["success"] is False
    assert out["status"] == "not_allowed_in_bootstrap"
    assert out["external_side_effects"] == "none"
    assert commits == []


def test_x_launch_post_not_deduped_on_wake(monkeypatch):
    import json as _json
    from plugins.takyon import core

    # A steady-state wake with an existing receipt must NOT be short-circuited by the bootstrap
    # dedupe — it falls through to the normal path (which then fails on the stubbed store, proving
    # the dedupe guard did not fire).
    monkeypatch.setattr(core, "_store", lambda: object())
    monkeypatch.setattr(core, "_resolved_business_slug", lambda args, required=False: "acme")
    monkeypatch.setattr(core, "_active_operator_task_kind", lambda: "ceo_wake")
    called = {"n": 0}

    def _candidates(store, business):
        called["n"] += 1
        return [{"post_id": "111"}]

    monkeypatch.setattr(core, "_x_outreach_receipt_candidates", _candidates)
    out = _json.loads(core._handle_live_business_x_publish_outreach({"business": "acme", "channel": "x", "provider": "x", "body": "hi"}))
    assert out.get("deduped") is not True
    assert called["n"] == 0  # the dedupe branch was never entered on a wake


def test_x_publish_metadata_accepts_only_runtime_owned_operator_task(monkeypatch):
    import contextlib
    import json as _json
    from plugins.takyon import core

    class _Store:
        @contextlib.contextmanager
        def _connect(self):
            yield object()

        @staticmethod
        def _ensure_business(_conn, _business):
            return {"mode": "live"}

    runtime_context: dict[str, object] = {}
    queued: list[dict] = []
    monkeypatch.setattr(core, "_store", _Store)
    monkeypatch.setattr(core, "_resolved_business_slug", lambda *_a, **_k: "acme")
    monkeypatch.setattr(core, "_active_operator_task_kind", lambda: "")
    monkeypatch.setattr(
        core,
        "_active_operator_task_receipt_context",
        lambda: dict(runtime_context),
    )
    monkeypatch.setattr(core, "_canonical_product_url", lambda *_a, **_k: "https://acme.coscale.app/")
    monkeypatch.setattr(core, "_creative_credit_preflight_gate", lambda *_a, **_k: {"success": True})

    def _capture(_args, operation, **_kwargs):
        queued.append(operation)
        return core.tool_result({"success": True})

    monkeypatch.setattr(core, "_run_worker_backed_business_job_and_wait", _capture)
    forged = {
        "task_kind": "ceo_bootstrap",
        "run_id": "caller-forged-job",
        "attempt": 99,
    }
    first = _json.loads(
        core._handle_live_business_x_publish_outreach(
            {
                "business": "acme",
                "channel": "x",
                "provider": "x",
                "body": "hello",
                "requires_api": ["x"],
                "idempotency_key": "x-no-runtime-context",
                "metadata": {"operator_task": forged, "campaign": "launch"},
            }
        )
    )
    assert first["success"] is True
    assert queued[-1]["payload"]["metadata"]["campaign"] == "launch"
    assert "operator_task" not in queued[-1]["payload"]["metadata"]

    runtime_context.update(
        {"task_kind": "ceo_bootstrap", "run_id": "runtime-job", "attempt": 2}
    )
    second = _json.loads(
        core._handle_live_business_x_publish_outreach(
            {
                "business": "acme",
                "channel": "x",
                "provider": "x",
                "body": "hello again",
                "requires_api": ["x"],
                "idempotency_key": "x-runtime-context",
                "metadata": {"operator_task": forged},
            }
        )
    )
    assert second["success"] is True
    assert queued[-1]["payload"]["metadata"]["operator_task"] == {
        "task_kind": "ceo_bootstrap",
        "run_id": "runtime-job",
        "attempt": 2,
    }


def test_x_bootstrap_refusal_precedes_creative_credit_gate(monkeypatch):
    import contextlib
    import json as _json
    from plugins.takyon import core

    class _Store:
        @contextlib.contextmanager
        def _connect(self):
            yield object()

        @staticmethod
        def _ensure_business(_conn, _business):
            return {"mode": "live"}

    recorded: dict[str, object] = {}
    runtime_context = {
        "task_kind": "ceo_bootstrap",
        "run_id": "bootstrap-job",
        "attempt": 3,
    }
    monkeypatch.setattr(core, "_store", _Store)
    monkeypatch.setattr(core, "_resolved_business_slug", lambda *_a, **_k: "acme")
    monkeypatch.setattr(core, "_active_operator_task_kind", lambda: "ceo_bootstrap")
    monkeypatch.setattr(core, "_active_operator_task_receipt_context", lambda: runtime_context)
    monkeypatch.setattr(core, "_x_outreach_receipt_candidates", lambda *_a, **_k: [])
    monkeypatch.setattr(core, "_canonical_product_url", lambda *_a, **_k: "https://acme.coscale.app/")
    monkeypatch.setattr(
        core,
        "_creative_credit_preflight_gate",
        lambda *_a, **_k: {
            "success": False,
            "status": "blocked_insufficient_creative_credits",
            "error": "insufficient_creative_credits",
        },
    )
    monkeypatch.setattr(
        core,
        "_record_scoped_bootstrap_x_outcome",
        lambda *_a, **kwargs: recorded.update(kwargs),
    )

    result = _json.loads(
        core._handle_live_business_x_publish_outreach(
            {
                "business": "acme",
                "channel": "x",
                "provider": "x",
                "body": "launch",
                "requires_api": ["x"],
                "idempotency_key": "x-credit-blocked",
            }
        )
    )

    assert result["success"] is False
    assert result["status"] == "not_allowed_in_bootstrap"
    assert result["external_side_effects"] == "none"
    assert recorded == {}
