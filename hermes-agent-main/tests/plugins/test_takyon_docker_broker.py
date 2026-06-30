from __future__ import annotations

import subprocess
from types import SimpleNamespace

from fastapi.testclient import TestClient

from plugins.takyon import docker_broker_app as broker_mod


def test_docker_broker_fails_closed_without_token(monkeypatch):
    monkeypatch.delenv("TAKYON_DOCKER_BROKER_TOKEN", raising=False)
    monkeypatch.delenv("TAKYON_SAFEBOX_TOKEN", raising=False)
    client = TestClient(broker_mod.build_docker_broker_app())

    response = client.post("/v1/version")

    assert response.status_code == 401


def test_docker_broker_run_detached_uses_real_docker_only_inside_service(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="container-123\n", stderr="")

    monkeypatch.setenv("TAKYON_DOCKER_BROKER_TOKEN", "shared-token")
    monkeypatch.setattr(broker_mod, "_docker_binary", lambda: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", fake_run)
    client = TestClient(broker_mod.build_docker_broker_app())

    response = client.post(
        "/v1/containers/run-detached",
        headers={"Authorization": "Bearer shared-token"},
        json={
            "args": ["-d", "--name", "takyon-test", "-w", "/workspace"],
            "image": "python:3.11",
            "command": ["sleep", "infinity"],
        },
    )

    assert response.status_code == 200
    assert response.json()["stdout"] == "container-123\n"
    assert calls == [[
        "/usr/bin/docker",
        "run",
        "-d",
        "--name",
        "takyon-test",
        "-w",
        "/workspace",
        "python:3.11",
        "sleep",
        "infinity",
    ]]


def test_docker_broker_rejects_unsupported_run_option(monkeypatch):
    monkeypatch.setenv("TAKYON_DOCKER_BROKER_TOKEN", "shared-token")
    monkeypatch.setattr(broker_mod, "_docker_binary", lambda: "/usr/bin/docker")
    client = TestClient(broker_mod.build_docker_broker_app())

    response = client.post(
        "/v1/containers/run-detached",
        headers={"Authorization": "Bearer shared-token"},
        json={
            "args": ["--privileged"],
            "image": "python:3.11",
            "command": ["sleep", "infinity"],
        },
    )

    assert response.status_code == 400
    assert "unsupported docker option" in response.text


def test_docker_broker_rejects_invalid_image(monkeypatch):
    monkeypatch.setenv("TAKYON_DOCKER_BROKER_TOKEN", "shared-token")
    monkeypatch.setattr(broker_mod, "_docker_binary", lambda: "/usr/bin/docker")
    client = TestClient(broker_mod.build_docker_broker_app())

    response = client.post(
        "/v1/containers/run-detached",
        headers={"Authorization": "Bearer shared-token"},
        json={
            "args": ["-d"],
            "image": "-v",
            "command": ["sleep", "infinity"],
        },
    )

    assert response.status_code == 400
    assert "invalid docker image" in response.text


def test_docker_broker_rejects_invalid_container_id(monkeypatch):
    monkeypatch.setenv("TAKYON_DOCKER_BROKER_TOKEN", "shared-token")
    monkeypatch.setattr(broker_mod, "_docker_binary", lambda: "/usr/bin/docker")
    client = TestClient(broker_mod.build_docker_broker_app())

    response = client.post(
        "/v1/containers/exec-attached",
        headers={"Authorization": "Bearer shared-token"},
        json={
            "args": ["-i"],
            "container_id": "--privileged",
            "command": ["sh"],
        },
    )

    assert response.status_code == 400
    assert "invalid container_id" in response.text
