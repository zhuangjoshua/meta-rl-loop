import subprocess
from pathlib import Path

from plugins.takyon import core as takyon_core
from plugins.takyon import product_activation_broker


def test_next_product_handoff_runs_remote_publish_as_takyon_user(tmp_path, monkeypatch):
    site = tmp_path / "source"
    site.mkdir(parents=True)
    (site / "package.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("TAKYON_PRODUCT_ACTIVATION_SSH_TARGET", "root@example")
    monkeypatch.setenv("TAKYON_PRODUCT_ACTIVATION_REMOTE_RUNTIME", "/opt/takyon/hermes-agent-main")
    monkeypatch.setenv("TAKYON_PRODUCT_ACTIVATION_REMOTE_HOME", "/opt/takyon/.takyon")

    captured: dict[str, object] = {}

    class _KeyFile:
        def __enter__(self):
            return tmp_path / "fake.key"

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(takyon_core, "_product_activation_ssh_key_file", lambda: _KeyFile())

    class _Completed:
        def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(cmd, **kwargs):
        if cmd[0] == "scp":
            captured["scp"] = cmd
            return _Completed()
        if cmd[0] == "ssh":
            captured["ssh"] = cmd
            return _Completed(stdout='{"status":"published","public_url":"https://latexflow.fourmanifold.com/"}\n')
        raise AssertionError(f"unexpected subprocess invocation: {cmd}")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = takyon_core._handoff_next_product_service_to_activation_host(
        source_root=site,
        slug="latexflow",
        publish_target="https://latexflow.fourmanifold.com/",
    )

    assert result["status"] == "published"
    ssh_cmd = captured["ssh"]
    assert isinstance(ssh_cmd, list)
    remote_script = ssh_cmd[-1]
    assert "runuser -u takyon -- env" in remote_script
    assert "HOME=/opt/takyon" in remote_script
    assert "TAKYON_HOME=/opt/takyon/.takyon" in remote_script


def test_next_product_publish_uses_local_broker_when_activation_surfaces_are_not_writable(tmp_path, monkeypatch):
    site = tmp_path / "source"
    site.mkdir(parents=True)

    monkeypatch.setattr(
        takyon_core,
        "_product_next_service_metadata",
        lambda _root: ({"start_command": ["npm", "run", "start"]}, ""),
    )
    monkeypatch.setattr(takyon_core, "_is_current_product_activation_node", lambda: True)
    monkeypatch.setattr(takyon_core, "_product_activation_surfaces_writable", lambda: False)

    captured: dict[str, object] = {}

    def _fake_publish_next_product_service(*, source_root: Path, slug: str, publish_target: str, source_path: str) -> dict[str, str]:
        captured["source_root"] = source_root
        captured["slug"] = slug
        captured["publish_target"] = publish_target
        captured["source_path"] = source_path
        return {
            "status": "published",
            "publish_target": publish_target,
            "public_url": publish_target,
            "published_at": "2026-06-10T00:00:00Z",
            "publish_root": "/opt/takyon/.takyon/product-services/tomato",
            "publish_source_path": "product/site",
            "blocker": "",
        }

    monkeypatch.setattr(product_activation_broker, "broker_enabled", lambda: True)
    monkeypatch.setattr(product_activation_broker, "publish_next_product_service", _fake_publish_next_product_service)

    result = takyon_core._publish_next_product_service(
        source_root=site,
        slug="tomato",
        publish_target="https://tomato.fourmanifold.com/",
    )

    assert result["status"] == "published"
    assert captured["source_root"] == site
    assert captured["slug"] == "tomato"
    assert captured["publish_target"] == "https://tomato.fourmanifold.com/"
    assert captured["source_path"] == "source"
