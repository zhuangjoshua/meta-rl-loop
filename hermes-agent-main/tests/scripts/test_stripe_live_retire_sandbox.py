from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "stripe_live_retire_sandbox.py"
_SPEC = importlib.util.spec_from_file_location("stripe_live_retire_sandbox", _PATH)
assert _SPEC and _SPEC.loader
cutover = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cutover)


def _valid_env(monkeypatch):
    monkeypatch.setattr(cutover.os, "geteuid", lambda: 0)
    monkeypatch.setenv("TAKYON_ENV", "prod")
    monkeypatch.setenv("TAKYON_HOST_ROLE", "operator")
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "test")
    monkeypatch.setenv("TAKYON_STRIPE_CHECKOUT_DISABLED", "1")
    monkeypatch.setenv("SSH_CONNECTION", "203.0.113.8 50000 10.0.0.1 22")


def test_root_ssh_operator_guard_accepts_only_paused_prod_sandbox(monkeypatch):
    _valid_env(monkeypatch)
    client, host = cutover._require_root_ssh_operator()
    assert client == "203.0.113.8"
    assert host


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("TAKYON_ENV", "dev", "prod_environment_required"),
        ("TAKYON_HOST_ROLE", "subuser", "operator_host_required"),
        ("TAKYON_STRIPE_MODE", "live", "sandbox_mode_required"),
        ("TAKYON_STRIPE_CHECKOUT_DISABLED", "0", "checkout_pause_required"),
        ("SSH_CONNECTION", "", "ssh_session_required"),
    ],
)
def test_root_ssh_operator_guard_fails_closed(monkeypatch, name, value, error):
    _valid_env(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=error):
        cutover._require_root_ssh_operator()


def test_finalize_rejects_any_other_account_pair_before_database(monkeypatch):
    _valid_env(monkeypatch)
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", cutover._EXPECTED_TARGET)
    with pytest.raises(RuntimeError, match="stripe_account_pair_mismatch"):
        cutover.finalize("acct_wrong", cutover._EXPECTED_TARGET)


def test_tracked_wrapper_stops_every_producer_and_proves_safebox_down():
    wrapper = (
        Path(__file__).resolve().parents[3]
        / "deploy"
        / "argon-alpha-14"
        / "retire-stripe-sandbox.sh"
    ).read_text()
    assert "systemctl stop takyon-safebox.service" in wrapper
    assert "systemctl stop takyon-subuser.service" in wrapper
    assert "134.209.123.8" in wrapper and "206.81.10.173" in wrapper
    assert "http://10.116.0.2:8000/healthz" in wrapper
    assert "systemctl is-active --quiet takyon-dashboard.service" not in wrapper
    assert "for unit in takyon-dashboard.service takyon-worker.service" in wrapper


def test_operator_deploy_orders_cutover_before_prod_service_restart():
    deploy = (
        Path(__file__).resolve().parents[3]
        / "deploy"
        / "argon-alpha-14"
        / "deploy-runtime.sh"
    ).read_text()
    activation = deploy.split("\nwait_for_remote_runtime_idle\n", 1)[1]
    stop = activation.index("TAKYON_STOP_CORE_SERVICES=1")
    migrate = activation.index("\nrun_remote_migrations\n")
    finalize = activation.index('if [[ "$TAKYON_FINALIZE_STRIPE_LIVE" == "1" ]]')
    restart = activation.index("systemctl restart takyon-dashboard.service")
    assert stop < migrate < finalize < restart
